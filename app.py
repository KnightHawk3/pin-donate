import json
import tornado.web
import tornado.ioloop
import logging
import decimal
import datetime
import uuid
import pymongo

from decimal import Decimal
from tornado import gen
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.web import StaticFileHandler, asynchronous, HTTPError
from tornado.options import define, options
from urllib.parse import urlencode
from log4mongo.handlers import MongoHandler
from mutiny.mongo import NonceManager

define("port", default=8888, help="Port number")
define("config", default="./config.json", help="Config file")
define("mode", default="testing", help="Deployment mode")

logger = logging.getLogger('pindonate')


class ReceiptManager:
    def __init__(self, collection):
        self.collection = collection

    def save(self, response):
        return self.collection.insert(response, safe=True)


class DonateHandler(tornado.web.RequestHandler):
    def get(self):
        self.render('templates/donate.html', **{
            "api_endpoint": self.application.settings['api_endpoint'],
            "publishable_key": self.application.settings['publishable_key'],
            "referer": self.request.headers.get('referer', None),
            "nonce": self.application.settings['nonces'].generate(),
            "mode": options.mode
        })

    @asynchronous
    @gen.coroutine
    def post(self):
        nonce = self.get_argument('nonce')
        if self.application.settings['nonces'].consume(nonce) is False:
            self.render("templates/expired.html", **{"mode": options.mode})
            return

        ip_address = self.get_argument('ip_address')
        card_token = self.get_argument('card_token')
        amount = self.get_argument('amount')
        email = self.get_argument('email')
        comment = self.get_argument('comment', None)


        try:
            # Convert dollar amount into cents
            amount = str((Decimal(amount) * Decimal(100))
                     .quantize(Decimal('1.'), rounding=decimal.ROUND_DOWN))
        except:
            logger.error("Invalid amount: %s" % amount)
            self.render("templates/error.html", **{"mode": options.mode})
            return

        currency = "AUD"
        description = "Pirate Party Donation"
        key = self.application.settings['secret_key']

        body = {
            "email": email,
            "ip_address": ip_address,
            "description": description,
            "amount": amount,
            "currency": currency,
            "card_token": card_token
        }

        http_client = AsyncHTTPClient()
        req = HTTPRequest("https://" + self.application.settings['api_endpoint'] + "/1/charges",
                method="POST",
                body=urlencode(body),
                auth_username=key
        )

        try:
            response = yield http_client.fetch(req)
        except Exception as e:
            if e.response.code != '422':
                logger.error("HTTP client request failed: %r" % e)
                logger.error(body)
                logger.error(e.response.body)
                self.render("templates/error.html", **{"mode": options.mode})
                return

        try:
            data = json.loads(response.body.decode())
            if data.get('response'):
                o = data['response']
                if comment is not None:
                    o['comment'] = comment
                self.application.settings['receipts'].save(o)

                str_amount = str(Decimal(data['response']['amount']) / Decimal(100))
                logger.info("Successful payment of $%s: %s (%s)" % (
                    str_amount, data['response']['token'], comment))
                self.render("templates/receipt.html", **{
                    "token": data['response']['token'],
                    "amount": str_amount,
                    "mode": options.mode
                })
                return
            elif data.get('error'):
                if data['error'] == "invalid_resource":
                    logger.warn("Rejected card: %r" % data)
                    self.render("templates/rejected.html", **{"mode": options.mode})
                    return
            else:
                raise Exception("Unexpected response body") # force exception
        except Exception as e:
            logger.error(e)
            logger.error("Response body: %r\nRaw response: %r\nEmail: %s" % (
                response.body, response, email))
            self.render("templates/error.html", **{"mode": options.mode})
            return

        self.finish()


if __name__ == "__main__":
    tornado.options.parse_command_line()
    settings = json.load(open(options.config))[options.mode]

    dbname = settings['db']
    logger.addHandler(MongoHandler(database_name=dbname))
    settings['nonces'] = NonceManager(pymongo.Connection()[dbname].nonces, expiry=30, logger=logger)
    settings['nonces'].clear_expired()
    settings['receipts'] = ReceiptManager(pymongo.Connection()[dbname].receipts)

    application = tornado.web.Application([
        (r"/static/(.*)", StaticFileHandler, {"path": "static"}),
        (r"/", DonateHandler)
    ], **settings)

    application.listen(options.port, xheaders=True)
    tornado.ioloop.IOLoop.instance().start()
