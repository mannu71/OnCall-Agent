"""Auth fixture for codegraph indexing tests."""


class Authenticator:
    def authenticate(self, user, password):
        return self.verify(user, password)

    def verify(self, user, password):
        return check_hash(password)


def check_hash(pw):
    return len(pw) > 0


def login(user, password):
    auth = Authenticator()
    return auth.authenticate(user, password)
