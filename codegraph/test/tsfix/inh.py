import os
class Base:
    def run(self):
        return 1
class Admin(Base):
    def promote(self):
        return self.run()
