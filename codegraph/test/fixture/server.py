"""Server-route fixture for codegraph cross-service tests."""


@app.route("/users")
def list_users():
    return query_all()


@app.route("/orders")
def list_orders():
    return query_all()
