from control_center_app.web import create_app
from waitress import serve
import os

def main():
    app = create_app()
    host = os.environ.get("CONTROL_CENTER_BIND", "127.0.0.1")
    port = int(os.environ.get("CONTROL_CENTER_PORT", "9000"))
    serve(app, host=host, port=port)

if __name__ == '__main__':
    main()
