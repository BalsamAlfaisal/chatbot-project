import json
import os
import uuid
from typing import List, Optional

import psycopg2
import chromadb
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from langchain_chroma import Chroma
from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI, OpenAIError
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

load_dotenv()

#stage 7 - load secrets from Azure Key Vault using the VM's managed identity
KEY_VAULT_NAME = os.environ.get("KEY_VAULT_NAME")
if KEY_VAULT_NAME:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    kv_client = SecretClient(
        vault_url=f"https://{KEY_VAULT_NAME}.vault.azure.net",
        credential=DefaultAzureCredential(),
    )
    KV_SECRETS = {
        "DB_NAME": "PROJ-DB-NAME",
        "DB_USER": "PROJ-DB-USER",
        "DB_PASSWORD": "PROJ-DB-PASSWORD",
        "DB_HOST": "PROJ-DB-HOST",
        "DB_PORT": "PROJ-DB-PORT",
        "OPENROUTER_API_KEY": "PROJ-OPENAI-API-KEY",
        "AZURE_STORAGE_SAS_URL": "PROJ-AZURE-STORAGE-SAS-URL",
        "AZURE_STORAGE_CONTAINER": "PROJ-AZURE-STORAGE-CONTAINER",
        "CHROMA_HOST": "PROJ-CHROMADB-HOST",
        "CHROMA_PORT": "PROJ-CHROMADB-PORT",
    }
    for env_name, secret_name in KV_SECRETS.items():
        os.environ[env_name] = kv_client.get_secret(secret_name).value
    print(f"Loaded {len(KV_SECRETS)} secrets from Key Vault {KEY_VAULT_NAME}")

print("DB_HOST:", repr(os.environ.get("DB_HOST")))
print("DB_PORT:", repr(os.environ.get("DB_PORT")))
print("DB_NAME:", repr(os.environ.get("DB_NAME")))
print("DB_USER:", repr(os.environ.get("DB_USER")))

DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME"),
    "user": os.environ.get("DB_USER"),
    "password": os.environ.get("DB_PASSWORD"),
    "host": os.environ.get("DB_HOST"),
    "port": os.environ.get("DB_PORT"),
}

# ADDED: these were missing and used below by llm/embeddings, causing NameError
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
api_key = os.environ.get("OPENROUTER_API_KEY")

client = OpenAI(
    api_key=os.environ.get("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)

#stage 3
model = "nvidia/nemotron-3-ultra-550b-a55b:free"

#stage 4 
DEFAULT_EMBEDDING_MODEL = "nvidia/nemotron-3-embed-1b:free"

llm = ChatOpenAI(model=model, base_url=OPENROUTER_BASE_URL, api_key=api_key)

embeddings = OpenAIEmbeddings(
    model=DEFAULT_EMBEDDING_MODEL,
    base_url=OPENROUTER_BASE_URL,
    api_key=api_key,
    # EXTRA (not in reference code) - commented out
    # check_embedding_ctx_length=False,
    # model_kwargs={"encoding_format": "float"},
)

# stage 4 - matches reference: uses a running Chroma server via HttpClient
# NOTE: this requires a separate Chroma server running on localhost:8000

# vectorstore = Chroma(
#     client=chroma_client,
#     collection_name="langchain",
#     embedding_function=embeddings,
# )


#stage 4.1
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost") 
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
vectorstore = Chroma( client=chroma_client, collection_name="pdf_chats", embedding_function=embeddings, ) 



# vectorstore = Chroma(
#     collection_name="pdf_chats",
#     embedding_function=embeddings,
#     persist_directory="chroma_data",
# )



app = FastAPI()

# Request models
class ChatRequest(BaseModel):
    messages: List[dict]

# OLD (duplicate, superseded by the stage 4 version below) - commented out, was causing conflicts
# class SaveChatRequest(BaseModel):
#     chat_id: str
#     chat_name: str
#     messages: List[dict]

class DeleteChatRequest(BaseModel):
    chat_id: str

# Dependency to manage database connection
def get_db():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


#stage 4
class SaveChatRequest(BaseModel):
    chat_id: str
    chat_name: str
    messages: List[dict]
    pdf_name: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_uuid: Optional[str] = None


class RAGChatRequest(BaseModel):
    messages: List[dict]
    pdf_uuid: str

#stage 4
@app.get("/load_chat/")
async def load_chat(db: psycopg2.extensions.connection = Depends(get_db)):
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT id, name, file_path, pdf_name, pdf_path, pdf_uuid "
                "FROM advanced_chats ORDER BY last_update DESC"
            )
            rows = cursor.fetchall()

        records = []
        for row in rows:
            chat_id, name, file_path = row["id"], row["name"], row["file_path"]
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    messages = json.load(f)
                records.append(
                    {
                        "id": chat_id,
                        "chat_name": name,
                        "messages": messages,
                        "pdf_name": row["pdf_name"],
                        "pdf_path": row["pdf_path"],
                        "pdf_uuid": row["pdf_uuid"],
                    }
                )

        return records

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/save_chat/")
async def save_chat(request: SaveChatRequest, db: psycopg2.extensions.connection = Depends(get_db)):
    try:
        file_path = f"chat_logs/{request.chat_id}.json"
        os.makedirs("chat_logs", exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(request.messages, f, ensure_ascii=False, indent=4)

        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO advanced_chats (id, name, file_path, last_update, pdf_path, pdf_name, pdf_uuid)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
                ON CONFLICT (id)
                DO UPDATE SET name = EXCLUDED.name, file_path = EXCLUDED.file_path, last_update = CURRENT_TIMESTAMP, pdf_path = EXCLUDED.pdf_path, pdf_name = EXCLUDED.pdf_name, pdf_uuid = EXCLUDED.pdf_uuid
                """,
                (request.chat_id, request.chat_name, file_path, request.pdf_path, request.pdf_name, request.pdf_uuid),
            )
        db.commit()
        return {"message": "Chat saved successfully"}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


#stage 4
@app.post("/upload_pdf/")
async def upload_pdf(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    try:
        pdf_uuid = str(uuid.uuid4())
        file_path = f"pdf_store/{pdf_uuid}_{file.filename}"
        os.makedirs("pdf_store", exist_ok=True)

        with open(file_path, "wb") as f:
            f.write(await file.read())

        # Load and process PDF
        loader = PyPDFLoader(file_path)
        documents = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        texts = text_splitter.split_documents(documents)

        # Add to Chroma
        vectorstore.add_texts(
            [doc.page_content for doc in texts],
            ids=[str(uuid.uuid4()) for _ in texts],
            metadatas=[{"pdf_uuid": pdf_uuid} for _ in texts],
        )

        return {"message": "File uploaded successfully", "pdf_path": file_path, "pdf_uuid": pdf_uuid}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")


#stage 4
@app.post("/rag_chat/")
async def rag_chat(request: RAGChatRequest):
    retriever = vectorstore.as_retriever(
        search_kwargs={"k": 5, "filter": {"pdf_uuid": request.pdf_uuid}}
    )

    ### Contextualize question ###
    contextualize_q_system_prompt = (
        "Given a chat history and the latest user question "
        "which might reference context in the chat history, "
        "formulate a standalone question which can be understood "
        "without the chat history. Do NOT answer the question, "
        "just reformulate it if needed and otherwise return it as is."
    )
    contextualize_q_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualize_q_system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    history_aware_retriever = create_history_aware_retriever(
        llm, retriever, contextualize_q_prompt
    )

    ### Answer question ###
    system_prompt = (
        "You are an assistant for question-answering tasks. "
        "Use the following pieces of retrieved context to answer "
        "the question. If you don't know the answer, say that you "
        "don't know. Use three sentences maximum and keep the "
        "answer concise."
        "\n\n"
        "{context}"
    )
    qa_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)

    rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)

    chat_history = []
    for message in request.messages[:-1]:
        if message["role"] == "user":
            chat_history.append(HumanMessage(content=message["content"]))
        if message["role"] == "assistant":
            chat_history.append(AIMessage(content=message["content"]))

    user_input = request.messages[-1]["content"]

    chain = rag_chain.pick("answer")

    def stream_response():
        for chunk in chain.stream({"chat_history": chat_history, "input": user_input}):
            yield chunk

    return StreamingResponse(stream_response(), media_type="text/plain")






@app.post("/delete_chat/")
async def delete_chat(request: DeleteChatRequest, db: psycopg2.extensions.connection = Depends(get_db)):
    try:
        file_path = None
        with db.cursor() as cursor:
            cursor.execute("SELECT file_path FROM advanced_chats WHERE id = %s", (request.chat_id,))
            result = cursor.fetchone()
            if result:
                file_path = result[0]
            else:
                raise HTTPException(status_code=404, detail="Chat not found")

        with db.cursor() as cursor:
            cursor.execute("DELETE FROM advanced_chats WHERE id = %s", (request.chat_id,))
        db.commit()

        if file_path and os.path.exists(file_path):
            os.remove(file_path)

        return {"message": "Chat deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")




@app.post("/chat/")
async def chat(request: ChatRequest):
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=request.messages,
            stream=True,
        )

        # if you don't want to stream the output
        # set the stream parameter to False in above function
        # and uncommnet the belowing line
        # return {"reply": response.choices[0].message.content}

        # Function to send out the stream data
        def stream_response():
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

        # Use StreamingResponse to return
        return StreamingResponse(stream_response(), media_type="text/plain")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Stage 3 (duplicate route, superseded by the stage 4 "/load_chat/" above which reads from advanced_chats) - commented out
# @app.get("/load_chat/")
# async def load_chat(db: psycopg2.extensions.connection = Depends(get_db)):
#     try:
#         with db.cursor(cursor_factory=RealDictCursor) as cursor:
#             cursor.execute("SELECT id, name, file_path FROM chats ORDER BY last_update DESC")
#             rows = cursor.fetchall()
#
#         records = []
#         for row in rows:
#             chat_id, name, file_path = row["id"], row["name"], row["file_path"]
#             if os.path.exists(file_path):
#                 with open(file_path, "r", encoding="utf-8") as f:
#                     messages = json.load(f)
#                 records.append({"id": chat_id, "chat_name": name, "messages": messages})
#
#         return records
#
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# Stage 3 (duplicate route, superseded by the stage 4 "/save_chat/" above which writes to advanced_chats) - commented out
# @app.post("/save_chat/")
# async def save_chat(request: SaveChatRequest, db: psycopg2.extensions.connection = Depends(get_db)):
#     try:
#         file_path = f"chat_logs/{request.chat_id}.json"
#         os.makedirs("chat_logs", exist_ok=True)
#         
#         # Save messages to file
#         with open(file_path, "w", encoding="utf-8") as f:
#             json.dump(request.messages, f, ensure_ascii=False, indent=4)
#         
#         # Insert or update database record
#         with db.cursor() as cursor:
#             cursor.execute(
#                 """
#                 INSERT INTO chats (id, name, file_path, last_update)
#                 VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
#                 ON CONFLICT (id)
#                 DO UPDATE SET name = EXCLUDED.name, file_path = EXCLUDED.file_path, last_update = CURRENT_TIMESTAMP
#                 """,
#                 (request.chat_id, request.chat_name, file_path),
#             )
#         db.commit()
#         return {"message": "Chat saved successfully"}
#     
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# Stage 3 (duplicate route, superseded by the stage 4 "/delete_chat/" above which deletes from advanced_chats) - commented out
# @app.post("/delete_chat/")
# async def delete_chat(request: DeleteChatRequest, db: psycopg2.extensions.connection = Depends(get_db)):
#     try:
#         # Retrieve the file path before deleting the record
#         file_path = None
#         with db.cursor() as cursor:
#             cursor.execute("SELECT file_path FROM chats WHERE id = %s", (request.chat_id,))
#             result = cursor.fetchone()
#             if result:
#                 file_path = result[0]
#             else:
#                 raise HTTPException(status_code=404, detail="Chat not found")
#
#         # Delete the record from the database
#         with db.cursor() as cursor:
#             cursor.execute("DELETE FROM chats WHERE id = %s", (request.chat_id,))
#         db.commit()
#
#         # Delete the associated file, if it exists
#         if file_path and os.path.exists(file_path):
#             os.remove(file_path)
#
#         return {"message": "Chat deleted successfully"}
#
#     except HTTPException:
#         # Reraise known exceptions
#         raise
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# # 
# @app.post("/chat/")
# async def chat(request: ChatRequest):
#     try:
#         response = client.chat.completions.create(
#             model=model,
#             messages=request.messages,
#         )

#         choices = getattr(response, "choices", None) or []
#         if not choices or not getattr(choices[0], "message", None):
#             raise HTTPException(
#                 status_code=502,
#                 detail="OpenRouter returned an empty or malformed response.",
#             )

#         content = choices[0].message.content or ""
#         return {"reply": content}
#     except HTTPException:
#         raise
#     except OpenAIError as error:
#         raise HTTPException(status_code=502, detail=str(error)) from error
#     except Exception as error:
#         raise HTTPException(
#             status_code=500,
#             detail=f"Unexpected server error: {error}",
#         ) from error