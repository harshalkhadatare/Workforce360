# gunicorn -c deploy/gunicorn.conf.py config.wsgi:application
import multiprocessing

bind = "127.0.0.1:8001"
workers = max(2, multiprocessing.cpu_count() * 2 + 1)
threads = 2
timeout = 120          # Excel imports run in-request; keep well below Nginx proxy_read_timeout
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = "127.0.0.1"
