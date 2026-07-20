import socket, sys
s = socket.create_connection(("127.0.0.1", 9151), timeout=5)
s.sendall(b"AUTHENTICATE \"toolkit2026\"\r\n")
r = s.recv(256)
if b"250" not in r: print("AUTH_FAIL", r); sys.exit(1)
s.sendall(b"SIGNAL NEWNYM\r\n")
r2 = s.recv(256)
s.close()
print("NEWNYM_OK" if b"250" in r2 else "NEWNYM_FAIL", r2)
