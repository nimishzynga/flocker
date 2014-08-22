from .._defer import gather_deferreds

from twisted.internet.defer import fail, FirstError, succeed
from twisted.python import log
from twisted.python.failure import Failure
from twisted.trial.unittest import TestCase


class GatherDeferredsTests(TestCase):
    """
    Tests for ``gather_deferreds``.
    """
    def test_success(self):
        """
        The successful results of the supplied ``deferreds`` are returned.
        """
        expected_result1 = object()
        expected_result2 = object()

        d = gather_deferreds(
            [succeed(expected_result1), succeed(expected_result2)])

        results = self.successResultOf(d)
        self.assertEqual([expected_result1, expected_result2], results)

    def test_consume_errors_true(self):
        """
        Errors in the supplied ``deferreds`` are always consumed so that they
        are not logged during garbage collection.
        """
        d = gather_deferreds(
            [fail(ZeroDivisionError('test_consume_errors1')),
             fail(ZeroDivisionError('test_consume_errors2'))])

        self.failureResultOf(d, FirstError)
        self.flushLoggedErrors(ZeroDivisionError)

    def test_fire_on_first_failure(self):
        """
        The first of the supplied list of ``deferreds`` to errback, causes the
        returned ``Deferred`` to errback with that failure.
        """
        expected_error = ZeroDivisionError('test_fire_on_first_failure1')
        d = gather_deferreds(
            [fail(expected_error),
             fail(ZeroDivisionError('test_fire_on_first_failure2'))])

        failure = self.failureResultOf(d, FirstError)
        self.assertEqual(expected_error, failure.value.subFailure.value)
        self.flushLoggedErrors(ZeroDivisionError)

    def test_logging(self):
        """
        Failures in the supplied ``deferreds`` are all logged.
        """
        messages = []
        log.addObserver(messages.append)
        self.addCleanup(log.removeObserver, messages.append)
        expected_failure1 = Failure(ZeroDivisionError('test_logging1'))
        expected_failure2 = Failure(ZeroDivisionError('test_logging2'))

        d = gather_deferreds(
            [fail(expected_failure1), fail(expected_failure2)])

        self.failureResultOf(d, FirstError)
        self.assertEqual(
            [expected_failure1, expected_failure2],
            self.flushLoggedErrors(ZeroDivisionError)
        )
