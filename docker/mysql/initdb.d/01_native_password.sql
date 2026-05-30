-- wikiuser を mysql_native_password に変更（pymysql の caching_sha2_password 非互換対応）
ALTER USER 'wikiuser'@'%' IDENTIFIED WITH mysql_native_password BY 'wikipassword';
FLUSH PRIVILEGES;
