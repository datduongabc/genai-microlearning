#!/bin/bash
set -e

# Migration chay o service "migrate" rieng trong devops/docker-compose.yml (backend cho
# no xong roi moi start), de tien trinh phuc vu request khong phai giu URL cua
# chu so huu database.
exec "$@"
