import aiosqlite
import json
import time
import uuid
from typing import Optional, List, Dict, Union, Any
from chainlit.data import BaseDataLayer
from chainlit.types import ThreadDict, FeedbackDict, Pagination, ThreadFilter, Feedback
from chainlit.step import StepDict
from chainlit.element import Element, ElementDict
from chainlit.user import User, PersistedUser

class SQLiteDataLayer(BaseDataLayer):
    def __init__(self, db_path: str = "chainlit.db"):
        import os
        # Ensure absolute path
        if not os.path.isabs(db_path):
            # Assume root of project if relative
            # But where is root? Let's use current working directory or better yet, same dir as brain.db
            # Let's use absolute path relative to CWD
            db_path = os.path.abspath(db_path)
            
        self.db_path = db_path
        self._initialized = False

    async def initialize_db(self):
        if self._initialized:
            return
            
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    identifier TEXT NOT NULL UNIQUE,
                    metadata TEXT,
                    createdAt TEXT
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    createdAt TEXT,
                    name TEXT,
                    userId TEXT,
                    userIdentifier TEXT,
                    tags TEXT,
                    metadata TEXT,
                    FOREIGN KEY (userId) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS steps (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    threadId TEXT NOT NULL,
                    parentId TEXT,
                    streaming BOOLEAN NOT NULL,
                    waitForAnswer BOOLEAN,
                    isError BOOLEAN,
                    metadata TEXT,
                    tags TEXT,
                    input TEXT,
                    output TEXT,
                    createdAt TEXT,
                    start TEXT,
                    end TEXT,
                    generation TEXT,
                    showInput TEXT,
                    language TEXT,
                    indent INTEGER,
                    FOREIGN KEY (threadId) REFERENCES threads(id) ON DELETE CASCADE
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS elements (
                    id TEXT PRIMARY KEY,
                    threadId TEXT,
                    type TEXT,
                    url TEXT,
                    chainlitKey TEXT,
                    name TEXT NOT NULL,
                    display TEXT,
                    objectKey TEXT,
                    size TEXT,
                    page INTEGER,
                    language TEXT,
                    forId TEXT,
                    mime TEXT,
                    props TEXT,
                    FOREIGN KEY (threadId) REFERENCES threads(id) ON DELETE CASCADE
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feedbacks (
                    id TEXT PRIMARY KEY,
                    forId TEXT NOT NULL,
                    threadId TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    comment TEXT,
                    FOREIGN KEY (threadId) REFERENCES threads(id) ON DELETE CASCADE
                )
            """)
            
            await db.commit()
            self._initialized = True

    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        print(f"[DEBUG] get_user called for identifier: {identifier}")
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE identifier = ?", (identifier,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    print(f"[DEBUG] get_user found: {row['id']}")
                    return PersistedUser(
                        id=row['id'],
                        identifier=row['identifier'],
                        metadata=json.loads(row['metadata']) if row['metadata'] else {},
                        createdAt=row['createdAt']
                    )
        print(f"[DEBUG] get_user not found for: {identifier}")
        return None

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        print(f"[DEBUG] create_user called for: {user.identifier}")
        await self.initialize_db()
        user_id = str(uuid.uuid4())
        # Use ISO 8601 format compatible with different platforms
        created_at = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        metadata = json.dumps(user.metadata) if user.metadata else "{}"
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO users (id, identifier, metadata, createdAt) VALUES (?, ?, ?, ?)",
                (user_id, user.identifier, metadata, created_at)
            )
            await db.commit()
            
        print(f"[DEBUG] create_user created id: {user_id}")
        return PersistedUser(
            id=user_id,
            identifier=user.identifier,
            metadata=user.metadata,
            createdAt=created_at
        )

    async def list_threads(self, pagination: Pagination, filter: ThreadFilter) -> Any:
        # returns PaginatedResponse[ThreadDict]
        print(f"[DEBUG] list_threads called. Filter userId: {filter.userId}, search: {filter.search}")
        await self.initialize_db()
        
        limit = pagination.first if pagination.first else 20
        offset = 0
        if pagination.cursor:
            try:
                offset = int(pagination.cursor)
            except:
                pass

        where_clauses = []
        params = []
        
        if filter.userId:
            where_clauses.append("userId = ?")
            params.append(filter.userId)
            
        if filter.search:
            where_clauses.append("name LIKE ?")
            params.append(f"%{filter.search}%")

        if filter.feedback:
            # Join with feedback table? Too complex for now, ignore
            pass
            
        where_str = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            query = f"SELECT * FROM threads {where_str} ORDER BY createdAt DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            threads = []
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    threads.append(ThreadDict(
                        id=row['id'],
                        createdAt=row['createdAt'],
                        name=row['name'],
                        userId=row['userId'],
                        userIdentifier=row['userIdentifier'],
                        tags=json.loads(row['tags']) if row['tags'] else [],
                        metadata=json.loads(row['metadata']) if row['metadata'] else {},
                        steps=[], # Steps loaded separately in get_thread
                        elements=[]
                    ))
            
            print(f"[DEBUG] list_threads found {len(threads)} threads")
            return type('PaginatedResponse', (), {
                'data': threads,
                'pageInfo': type('PageInfo', (), {
                    'hasNextPage': len(threads) == limit,
                    'endCursor': str(offset + limit),
                    'to_dict': lambda self: {
                        'hasNextPage': self.hasNextPage,
                        'endCursor': self.endCursor
                    }
                })(),
                'to_dict': lambda self: {
                    'data': [t for t in self.data],
                    'pageInfo': self.pageInfo.to_dict()
                }
            })()

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Get Thread
            async with db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)) as cursor:
                thread_row = await cursor.fetchone()
                if not thread_row:
                    return None
            
            # Get Steps
            steps = []
            async with db.execute("SELECT * FROM steps WHERE threadId = ? ORDER BY createdAt ASC", (thread_id,)) as cursor:
                async for row in cursor:
                    steps.append(StepDict(
                        id=row['id'],
                        name=row['name'],
                        type=row['type'],
                        threadId=row['threadId'],
                        parentId=row['parentId'],
                        streaming=bool(row['streaming']),
                        waitForAnswer=bool(row['waitForAnswer']),
                        isError=bool(row['isError']),
                        metadata=json.loads(row['metadata']) if row['metadata'] else {},
                        tags=json.loads(row['tags']) if row['tags'] else [],
                        input=row['input'],
                        output=row['output'],
                        createdAt=row['createdAt'],
                        start=row['start'],
                        end=row['end'],
                        generation=json.loads(row['generation']) if row['generation'] else None,
                        showInput=row['showInput'],
                        language=row['language'],
                        indent=row['indent']
                    ))

            # Get Elements
            elements = []
            async with db.execute("SELECT * FROM elements WHERE threadId = ?", (thread_id,)) as cursor:
                async for row in cursor:
                    elements.append(ElementDict(
                        id=row['id'],
                        threadId=row['threadId'],
                        type=row['type'],
                        url=row['url'],
                        chainlitKey=row['chainlitKey'],
                        name=row['name'],
                        display=row['display'],
                        objectKey=row['objectKey'],
                        size=row['size'],
                        page=row['page'],
                        language=row['language'],
                        forId=row['forId'],
                        mime=row['mime'],
                        props=json.loads(row['props']) if row['props'] else {}
                    ))

            return ThreadDict(
                id=thread_row['id'],
                createdAt=thread_row['createdAt'],
                name=thread_row['name'],
                userId=thread_row['userId'],
                userIdentifier=thread_row['userIdentifier'],
                tags=json.loads(thread_row['tags']) if thread_row['tags'] else [],
                metadata=json.loads(thread_row['metadata']) if thread_row['metadata'] else {},
                steps=steps,
                elements=elements
            )

    async def create_thread(self, thread_dict: ThreadDict) -> Optional[str]:
        print(f"[DEBUG] create_thread called. ID: {thread_dict['id']}, userId: {thread_dict.get('userId')}, userIdentifier: {thread_dict.get('userIdentifier')}")
        await self.initialize_db()
        
        tags = json.dumps(thread_dict.get('tags', []))
        metadata = json.dumps(thread_dict.get('metadata', {}))
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO threads (id, createdAt, name, userId, userIdentifier, tags, metadata) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    thread_dict['id'],
                    thread_dict.get('createdAt'),
                    thread_dict.get('name'),
                    thread_dict.get('userId'),
                    thread_dict.get('userIdentifier'),
                    tags,
                    metadata
                )
            )
            await db.commit()
        return thread_dict['id']

    async def update_thread(self, thread_id: str, name: Optional[str] = None, user_id: Optional[str] = None, metadata: Optional[Dict] = None, tags: Optional[List[str]] = None):
        print(f"[DEBUG] update_thread called for {thread_id}. userId: {user_id}")
        await self.initialize_db()
        
        # Prepare data for upsert
        import time
        created_at = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        
        metadata_json = json.dumps(metadata) if metadata else "{}"
        tags_json = json.dumps(tags) if tags else "[]"
        
        async with aiosqlite.connect(self.db_path) as db:
            # Upsert logic: Try to insert, if exists (primary key conflict), then update
            # We use INSERT OR IGNORE then UPDATE for SQLite compatibility or explicit upsert syntax
            
            # Using standard SQLite UPSERT syntax (requires SQLite 3.24+)
            await db.execute("""
                INSERT INTO threads (id, createdAt, name, userId, metadata, tags)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = COALESCE(excluded.name, threads.name),
                    userId = COALESCE(excluded.userId, threads.userId),
                    metadata = CASE WHEN excluded.metadata != '{}' THEN excluded.metadata ELSE threads.metadata END,
                    tags = CASE WHEN excluded.tags != '[]' THEN excluded.tags ELSE threads.tags END
            """, (thread_id, created_at, name, user_id, metadata_json, tags_json))
            
            await db.commit()
            
    async def get_user_threads(self, pagination: Pagination, filter: ThreadFilter) -> Any:
        return await self.list_threads(pagination, filter)

    async def delete_thread(self, thread_id: str):
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            await db.commit()

    async def create_step(self, step_dict: StepDict):
        print(f"[DEBUG] create_step called for thread {step_dict['threadId']}. Type: {step_dict['type']}")
        await self.initialize_db()
        
        metadata = json.dumps(step_dict.get('metadata', {}))
        tags = json.dumps(step_dict.get('tags', []))
        generation = json.dumps(step_dict.get('generation')) if step_dict.get('generation') else None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO steps (id, name, type, threadId, parentId, streaming, waitForAnswer, isError, metadata, tags, input, output, createdAt, start, end, generation, showInput, language, indent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   input = excluded.input,
                   output = excluded.output,
                   end = excluded.end,
                   generation = excluded.generation
                   """,
                (
                    step_dict['id'],
                    step_dict['name'],
                    step_dict['type'],
                    step_dict['threadId'],
                    step_dict.get('parentId'),
                    step_dict.get('streaming', False),
                    step_dict.get('waitForAnswer', False),
                    step_dict.get('isError', False),
                    metadata,
                    tags,
                    step_dict.get('input', ''),
                    step_dict.get('output', ''),
                    step_dict.get('createdAt'),
                    step_dict.get('start'),
                    step_dict.get('end'),
                    generation,
                    step_dict.get('showInput'),
                    step_dict.get('language'),
                    step_dict.get('indent')
                )
            )
            # Update thread timestamp
            if step_dict.get('createdAt'):
                await db.execute(
                    "UPDATE threads SET createdAt = ? WHERE id = ?",
                    (step_dict['createdAt'], step_dict['threadId'])
                )
            
            await db.commit()

    async def update_step(self, step_dict: StepDict):
        await self.initialize_db()
        
        updates = []
        params = []
        
        fields = ['name', 'type', 'input', 'output', 'start', 'end', 'showInput', 'language', 'indent']
        for f in fields:
            if f in step_dict and step_dict[f] is not None:
                updates.append(f"{f} = ?")
                params.append(step_dict[f])
                
        bool_fields = ['streaming', 'waitForAnswer', 'isError']
        for f in bool_fields:
            if f in step_dict:
                updates.append(f"{f} = ?")
                params.append(step_dict[f])

        if 'metadata' in step_dict and step_dict['metadata'] is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(step_dict['metadata']))
            
        if 'tags' in step_dict and step_dict['tags'] is not None:
            updates.append("tags = ?")
            params.append(json.dumps(step_dict['tags']))
            
        if 'generation' in step_dict and step_dict['generation'] is not None:
            updates.append("generation = ?")
            params.append(json.dumps(step_dict['generation']))

        if not updates:
            return

        params.append(step_dict['id'])
        query = f"UPDATE steps SET {', '.join(updates)} WHERE id = ?"
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(query, params)
            await db.commit()

    async def delete_step(self, step_id: str):
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM steps WHERE id = ?", (step_id,))
            await db.commit()

    async def create_element(self, element: Element):
        await self.initialize_db()
        element_dict = element.to_dict()
        props = json.dumps(element_dict.get('props', {}))
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO elements (id, threadId, type, url, chainlitKey, name, display, objectKey, size, page, language, forId, mime, props)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    element_dict['id'],
                    element_dict.get('threadId'),
                    element_dict.get('type'),
                    element_dict.get('url'),
                    element_dict.get('chainlitKey'),
                    element_dict.get('name'),
                    element_dict.get('display'),
                    element_dict.get('objectKey'),
                    element_dict.get('size'),
                    element_dict.get('page'),
                    element_dict.get('language'),
                    element_dict.get('forId'),
                    element_dict.get('mime'),
                    props
                )
            )
            await db.commit()

    async def get_element(self, thread_id: str, element_id: str) -> Optional[ElementDict]:
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM elements WHERE id = ? AND threadId = ?", (element_id, thread_id)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return ElementDict(
                        id=row['id'],
                        threadId=row['threadId'],
                        type=row['type'],
                        url=row['url'],
                        chainlitKey=row['chainlitKey'],
                        name=row['name'],
                        display=row['display'],
                        objectKey=row['objectKey'],
                        size=row['size'],
                        page=row['page'],
                        language=row['language'],
                        forId=row['forId'],
                        mime=row['mime'],
                        props=json.loads(row['props']) if row['props'] else {}
                    )
        return None

    async def delete_element(self, element_id: str, thread_id: Optional[str] = None):
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM elements WHERE id = ?", (element_id,))
            await db.commit()

    async def delete_feedback(self, feedback_id: str) -> bool:
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM feedbacks WHERE id = ?", (feedback_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def upsert_feedback(self, feedback: Feedback) -> str:
        await self.initialize_db()
        
        feedback_id = feedback.id or str(uuid.uuid4())
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO feedbacks (id, forId, threadId, value, comment)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   value = excluded.value,
                   comment = excluded.comment
                """,
                (
                    feedback_id,
                    feedback.forId,
                    feedback.threadId,
                    feedback.value,
                    feedback.comment
                )
            )
            await db.commit()
        return feedback_id

    async def get_thread_author(self, thread_id: str) -> str:
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT userIdentifier FROM threads WHERE id = ?", (thread_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row['userIdentifier']:
                    return row['userIdentifier']
        return ""

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        pass

    async def get_favorite_steps(self, user_id: str) -> List[StepDict]:
        return []
