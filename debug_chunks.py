from personal_brain.core.database import get_db_connection
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("SELECT id, content FROM file_chunks LIMIT 3")
rows = cursor.fetchall()
if not rows:
    print("No chunks found.")
else:
    for row in rows:
        print(f"Chunk ID: {row['id']}")
        print(f"Content Length: {len(row['content']) if row['content'] else 0}")
        print(f"Content Preview: {row['content'][:50] if row['content'] else 'None'}")
