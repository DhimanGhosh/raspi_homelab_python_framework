# Music Player v7.1.2

Docker-fix build that avoids shell-based build steps inside the image.
Uses a standard-library Python HTTP server so the image build only needs COPY/CMD.
