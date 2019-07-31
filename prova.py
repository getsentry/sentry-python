
import os

import sentry_sdk
import sentry_sdk.integrations.trytond

sentry_sdk.init(
    os.environ['SENTRY_DSN'],
    integrations=[
        sentry_sdk.integrations.trytond.TrytondWSGIIntegration(),
    ]
)


from trytond.application import app
from trytond.exceptions import UserError as TrytondUserError

from trytond.pool import PoolMeta, Pool
from trytond.model import Model
from trytond.protocols.wrappers import user_application
from trytond.protocols.wrappers import with_pool, with_transaction


app.append_err_handler(
    sentry_sdk.integrations.trytond.rpc_error_page
)


class MyModel(Model):
    __name__ = 'mymodel'

    @classmethod
    def cronfail(cls):
        raise Exception('unhandled')
        raise TrytondUserError('err message', 'error details')


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super(Cron, cls).__setup__()
        cls.method.selection += [
            ('mymodel|cronfail', 'Fail'),
        ]


class UserApplication(metaclass=PoolMeta):
    __name__ = 'res.user.application'

    @classmethod
    def __setup__(cls):
        super(UserApplication, cls).__setup__()
        cls.application.selection.append(('webapp', 'WebApp'))


Pool.register(
    MyModel,
    Cron,
    UserApplication,
    module='res',
    type_='model'
)


@app.route('/myroute/fail', methods=['GET'])
def _route_fail(request):
    raise Exception('myroute')


@app.route('/<string:database_name>/fail', methods=['GET'])
@with_pool
@with_transaction()
def _pool_fail(request, pool):
    raise Exception('pool')


@app.route('/<string:database_name>/webapp/fail', methods=['GET'])
@with_pool
@with_transaction()
@user_application('webapp')
def _userapp_fail(request, pool):
    raise Exception('webapp')
