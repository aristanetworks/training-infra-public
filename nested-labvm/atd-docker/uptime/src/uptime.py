#!/usr/bin/env python3

import tornado.ioloop
import tornado.web
import psutil
import time
from datetime import datetime, timedelta
from os.path import exists

APP_PORT = 50010

class UptimeHandler(tornado.web.RequestHandler):
    def get(self):
        self.write({
            'uptime': uptimeSeconds(),
            'boottime': round(psutil.boot_time()),
            'status': checkProvisioned('/etc/atd/.init')
        })


def uptimeSeconds():
    return(round(time.time() - psutil.boot_time()))

def checkProvisioned(full_file_path):
    if exists(full_file_path):
        return('init')
    else:
        return('post')

def pS(mtype):
    """
    Function to send output from service file to Syslog
    """
    cur_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mmes = "\t" + mtype
    print("[{0}] {1}".format(cur_dt, mmes.expandtabs(7 - len(cur_dt))))


if __name__ == "__main__":
    app = tornado.web.Application([
        (r'/uptime', UptimeHandler)
    ])
    app.listen(APP_PORT)
    pS('*** Server Started on {} ***'.format(APP_PORT))
    try:
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        pS("*** Server Stopped ***")