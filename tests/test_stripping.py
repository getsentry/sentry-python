from sentry_sdk.stripping import AnnotatedValue, flatten_metadata, strip_databag

def test_flatten_metadata():
    assert flatten_metadata({'foo': u'bar'}) == {'foo': u'bar'}
    assert flatten_metadata({'foo': ['bar']}) == {'foo': [u'bar']}
    assert flatten_metadata({'foo': [AnnotatedValue('bar', u'meta')]}) == {
        'foo': [u'bar'],
        '': {'foo': {0: {'': u'meta'}}}
    }


def test_strip_databag():
    d = strip_databag({'foo': u'a' * 2000})
    assert len(d['foo'].value) == 512
