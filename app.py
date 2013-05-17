import json
import tornado.web
import tornado.ioloop
import logging
import tornado.gen
import decimal

from decimal import Decimal
from tornado.httpserver import HTTPServer
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.web import StaticFileHandler, asynchronous
from tornado.options import define, options
from urllib.parse import urlencode

define("config", default="./config.json", help="Config file")
define("mode", default="testing", help="Deployment mode")

class DonateHandler(tornado.web.RequestHandler):
    def get(self):
        self.render('templates/donate.html', **{
            "api_endpoint": self.application.settings['api_endpoint'],
            "publishable_key": self.application.settings['publishable_key'],
            "referer": self.request.headers.get('referer', None)
        })

    @asynchronous
    @tornado.gen.engine
    def post(self):
        ip_address = self.get_argument('ip_address')
        card_token = self.get_argument('card_token')
        amount = self.get_argument('amount')
        email = self.get_argument('email')

        # Convert dollar amount into cents
        amount = str((Decimal(amount) * Decimal(100))
                   .quantize(Decimal('1.'), rounding=decimal.ROUND_DOWN))

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
        logging.debug(repr(body))

        http_client = AsyncHTTPClient()

        req = HTTPRequest("https://" + self.application.settings['api_endpoint'] + "/1/charges",
                method="POST",
                body=urlencode(body),
                auth_username=key
        )
        response = yield tornado.gen.Task(http_client.fetch, req)
        logging.debug("%r" % response)
        logging.debug("%r" % response.body)

        self.finish()


if __name__ == "__main__":
    tornado.options.parse_command_line()
    settings = json.load(open(options.config))[options.mode]

    application = tornado.web.Application([
        (r"/static/(.*)", StaticFileHandler, {"path": "static"}),
        (r"/", DonateHandler)
    ], **settings)

    http_server = HTTPServer(application)
    http_server.listen(8888)
    tornado.ioloop.IOLoop.instance().start()
