import json
import tornado.web
import tornado.ioloop
import logging
import decimal
import datetime
import uuid
import pymongo
import stripe

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

logger = logging.getLogger('stripedonate')

# Set your secret key: remember to change this to your live secret key in production
# See your keys here https://dashboard.stripe.com/account/apikeys
stripe.api_key = "sk_test_agShWnjOFPmnU1jQeM501Smd"


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

        token = self.get_argument('stripeToken')
        nonce = self.get_argument('nonce')

        if self.application.settings['nonces'].consume(nonce) is False:
            self.render("templates/expired.html", **{"mode": options.mode})
            return

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


        currency = "aud"
        description = "Pirate Party Donation"
        body = {
            "email": email,
            "description": description,
            "amount": amount,
            "currency": currency,
        }

        # Create the charge on Stripe's servers - this will charge the user's card
        try:
            charge = stripe.Charge.create(
                amount=amount, # amount in cents, again
                currency=currency,
                source=token,
                description=description
            )

            # self.application.settings['receipts'].save(o)

            str_amount = str(Decimal(charge.amount) / Decimal(100))
            logger.info("Successful payment of $%s: %s (%s)" % (
                str_amount, charge.source.id, comment))
            self.render("templates/receipt.html", **{
                "token": charge.source.id,
                "amount": str_amount,
                "mode": options.mode
            })
        except stripe.error.CardError as e:
            # The card has been declined
            err  = body['error']
            print("Status is: %s" % e.http_status)
            print("Type is: %s" % err['type'])
            logger.warn("Rejected card: %r" % err['code'])
            self.render("templates/rejected.html", **{"mode": options.mode})
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
