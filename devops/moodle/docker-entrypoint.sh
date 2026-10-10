#!/bin/bash
set -e

# Moodle 5.x: config.php THAT nam o goc, con public/config.php chi la stub do
# Moodle phat hanh san de nap no. Phai kiem tra ban o goc; kiem tra ban stub thi
# lan nao cung thay co va khong bao gio cai dat duoc.
#
# config.php nam tren bind mount ./moodle (host) -- song sot qua
# restart/rebuild container. Nhung config.php ton tai KHONG dong nghia da
# cai xong: admin/cli/install.php ghi config.php truoc khi kiem tra moi
# truong/cai bang, nen 1 lan chay bi loi giua chung (vd thieu php.ini
# setting) van de lai config.php "mo coi" -- tables chua bao gio duoc tao.
# Vi vay danh sach nhanh phu thuoc trang thai thuc te, khong chi dua vao
# config.php co ton tai hay khong:
#   1) Chua co config.php            -> cai day du (install.php)
#   2) Co config.php, chua co bang   -> chi hoan tat phan DB (install_database.php)
#   3) Co config.php VA da co bang   -> khong lam gi, start Apache luon
if [ ! -f /var/www/html/config.php ]; then
    # Truoc khi ket luan la "chua cai", hoi co so du lieu da. Co truong hop
    # config.php mat nhung DB van con nguyen bang, luc do cai moi se that bai.
    set +e
    php /usr/local/bin/restore-config.php
    restore_exit=$?
    set -e
    if [ $restore_exit -eq 2 ]; then
        exit 1
    fi
fi

if [ ! -f /var/www/html/config.php ]; then
    echo "[moodle-entrypoint] Chua co config.php, chay CLI installer day du..."
    php /var/www/html/admin/cli/install.php \
        --non-interactive \
        --agree-license \
        --allow-unstable \
        --lang=en \
        --wwwroot="${MOODLE_WWWROOT}" \
        --dataroot=/var/www/moodledata \
        --dbtype=pgsql \
        --dbhost="${MOODLE_DB_HOST}" \
        --dbport="${MOODLE_DB_PORT}" \
        --dbname="${MOODLE_DB_NAME}" \
        --dbuser="${MOODLE_DB_USER}" \
        --dbpass="${MOODLE_DB_PASSWORD}" \
        --fullname="${MOODLE_SITE_NAME}" \
        --shortname="${MOODLE_SITE_SHORTNAME:-Microlearning}" \
        --adminuser="${MOODLE_ADMIN_USER}" \
        --adminpass="${MOODLE_ADMIN_PASSWORD}" \
        --adminemail="${MOODLE_ADMIN_EMAIL}"
    echo "[moodle-entrypoint] Cai dat xong."
else
    # Kiem ket noi truoc bang chinh thong tin trong config.php. Khong co buoc nay thi mat
    # khau sai, database khong ton tai hay may chu chua len deu chi hien ra thanh cau
    # "Database connection failed" cua Moodle, roi bi ket luan la "ly do khac".
    if ! php /usr/local/bin/check-database.php /var/www/html/config.php; then
        echo "[moodle-entrypoint] Dung lai: khong ket noi duoc database, xem ly do o tren." >&2
        exit 1
    fi
    echo "[moodle-entrypoint] config.php da ton tai, kiem tra xem bang DB da co chua..."
    set +e
    php /var/www/html/admin/cli/install_database.php \
        --agree-license \
        --fullname="${MOODLE_SITE_NAME}" \
        --shortname="${MOODLE_SITE_SHORTNAME:-Microlearning}" \
        --adminuser="${MOODLE_ADMIN_USER}" \
        --adminpass="${MOODLE_ADMIN_PASSWORD}" \
        --adminemail="${MOODLE_ADMIN_EMAIL}" \
        > /tmp/install_database.log 2>&1
    db_install_exit=$?
    set -e
    cat /tmp/install_database.log
    if [ $db_install_exit -eq 0 ]; then
        echo "[moodle-entrypoint] Hoan tat phan DB con thieu tu lan chay truoc."
    elif grep -q "already present" /tmp/install_database.log; then
        echo "[moodle-entrypoint] Da cai dat day du tu truoc, bo qua."
    else
        echo "[moodle-entrypoint] install_database.php that bai vi ly do khac (xem log tren)."
        exit 1
    fi
fi

# Chay unconditionally moi lan container start (khong chi lan cai dau) --
# day la buoc "Site administration > Notifications" tuong duong ban CLI:
# phat hien plugin moi/thay doi (vd local_lms_bridge them vao sau) va tu
# tao bang/dang ky capability/WS function cho no. No-op an toan neu khong
# co gi thay doi tu lan chay truoc.
echo "[moodle-entrypoint] Chay upgrade.php de nhan dien plugin moi/thay doi..."
php /var/www/html/admin/cli/upgrade.php --non-interactive --allow-unstable

# Neu lan cai truoc bi kill giua chung (bang da tao nhung cac buoc cuoi cua
# install_database.php chua chay), nhanh o tren van bao "da cai xong" -- admin
# ket o mat khau giu cho (khong dang nhap duoc, Web Services loi "User not
# fully set-up"), thieu cau hinh mac dinh (course moi khong co phuong thuc
# ghi danh) va chua bat vai tro (rolesactive=0: moi ham web service, ke ca cua
# admin, bao "Access control exception"). Script nay la no-op tren site da on.
php /usr/local/bin/finish-interrupted-install.php

# Bat web service + REST va tao token backend dang cau hinh (MOODLE_TOKEN) neu chua co,
# de Moodle moi (vd sau khi tao lai volume) dung duoc ngay ma khong phai bam tay trong
# trang quan tri. No-op tren site da co.
php /usr/local/bin/configure-webservice.php

php /usr/local/bin/configure-course-categories.php

chown -R www-data:www-data /var/www/moodledata

exec "$@"
