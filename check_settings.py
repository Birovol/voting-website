import sqlite3

conn = sqlite3.connect('instance/site.db')
cur = conn.cursor()
cur.execute('SELECT key, value FROM settings WHERE key IN (?, ?, ?)', ("use_background", "background_image", "header_image"))
results = cur.fetchall()
conn.close()

print("Background settings:")
for key, value in results:
    print(f"{key}: {value}")
