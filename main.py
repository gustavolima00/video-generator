from flask_server import app as flask_server

def run_server():
    flask_server.app.run(debug=True)

if __name__ == "__main__":
    run_server()