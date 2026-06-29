"""Ambiguity fixture: two classes with identically-named methods.

A call resolver that links by short name alone will connect each save() to BOTH
validate() methods (50% precision). A scope-aware resolver uses the self/class
context to link each save() to its OWN class's validate() (100% precision).
"""


class UserService:
    def save(self):
        return self.validate()

    def validate(self):
        return True


class OrderService:
    def save(self):
        return self.validate()

    def validate(self):
        return False


def run():
    u = UserService()
    return u.save()
