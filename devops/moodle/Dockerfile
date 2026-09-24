FROM php:8.3-apache

ADD --chmod=0755 https://github.com/mlocati/docker-php-extension-installer/releases/latest/download/install-php-extensions /usr/local/bin/

RUN install-php-extensions \
    iconv mbstring curl ctype zip gd simplexml dom xml xmlreader \
    intl fileinfo sodium pdo_pgsql pgsql soap exif \
    && a2enmod rewrite

COPY devops/moodle/moodle-recommended.ini /usr/local/etc/php/conf.d/moodle-recommended.ini

ENV APACHE_DOCUMENT_ROOT=/var/www/html/public

RUN sed -ri -e "s!/var/www/html!${APACHE_DOCUMENT_ROOT}!g" \
    /etc/apache2/sites-available/*.conf /etc/apache2/apache2.conf

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

RUN mkdir -p /var/www/moodledata \
    && chown -R www-data:www-data /var/www/moodledata

COPY devops/moodle/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY devops/moodle/finish-interrupted-install.php /usr/local/bin/finish-interrupted-install.php
COPY devops/moodle/configure-webservice.php /usr/local/bin/configure-webservice.php
COPY devops/moodle/restore-config.php /usr/local/bin/restore-config.php
COPY devops/moodle/check-database.php /usr/local/bin/check-database.php

VOLUME /var/www/moodledata

ENTRYPOINT ["bash", "/usr/local/bin/docker-entrypoint.sh"]

CMD ["apache2-foreground"]
