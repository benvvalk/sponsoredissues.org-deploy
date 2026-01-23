import logging

class PrefixLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        assert self.extra
        assert 'prefix' in self.extra
        return f'{self.extra["prefix"]}{msg}', kwargs