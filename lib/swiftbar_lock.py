"""The file itself holds the lock; closing it also releases it after a crash."""
import os

if os.name != 'nt':
    from fcntl import LOCK_EX, LOCK_NB, flock
else:
    import errno
    import msvcrt
    import time

    LOCK_EX = 1
    LOCK_NB = 2

    def flock(stream, flags):
        stream.seek(0)
        while True:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if flags & LOCK_NB:
                    raise BlockingIOError(errno.EAGAIN, 'file locked') from error
                time.sleep(.05)

__all__ = ['LOCK_EX', 'LOCK_NB', 'flock']
