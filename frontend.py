import requests
import streamlit as st
import uuid

#stage 4.1
import os 
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000") 

st.title("Chatbot (frontend + backend)")

# stage 3: track multiple chats in session state
if "history_chats" not in st.session_state:
    st.session_state["history_chats"] = []
    # [{"id": "chat_id", "messages": [...]}, ...]
if "current_chat" not in st.session_state:
    st.session_state["current_chat"] = None
if "chat_names" not in st.session_state:
    st.session_state["chat_names"] = {}
if "chats_loaded" not in st.session_state:
    st.session_state["chats_loaded"] = False


def load_chats_from_db():
    try:
        response = requests.get(f"{BACKEND_URL}/load_chat/")
        response.raise_for_status()
    except requests.RequestException as error:
        st.error(f"Could not reach the backend: {error}")
        return

    for record in response.json():
        chat_id = record["id"]
        st.session_state["history_chats"].append(
            {"id": chat_id, "messages": record["messages"]}
        )
        st.session_state["chat_names"][chat_id] = record["chat_name"]


if not st.session_state["chats_loaded"]:
    load_chats_from_db()
    st.session_state["chats_loaded"] = True


def save_chat_to_db(chat_id, chat_name, messages):
    payload = {
        "chat_id": chat_id,
        "chat_name": chat_name,
        "messages": messages,
    }
    try:
        response = requests.post(f"{BACKEND_URL}/save_chat/", json=payload)
        response.raise_for_status()
    except requests.RequestException as error:
        st.error(f"Failed to save chat: {error}")


def create_chat(chat_name):
    new_chat_id = str(uuid.uuid4())
    new_chat = {"id": new_chat_id, "messages": []}
    st.session_state["history_chats"].insert(0, new_chat)
    st.session_state["chat_names"][new_chat_id] = chat_name
    st.session_state["current_chat"] = new_chat_id
    save_chat_to_db(new_chat_id, chat_name, [])


def delete_chat():
    if st.session_state["current_chat"]:
        chat_id = st.session_state["current_chat"]
        st.session_state["history_chats"] = [
            chat for chat in st.session_state["history_chats"] if chat["id"] != chat_id
        ]
        del st.session_state["chat_names"][chat_id]
        try:
            response = requests.post(f"{BACKEND_URL}/delete_chat/", json={"chat_id": chat_id})
            response.raise_for_status()
        except requests.RequestException as error:
            st.error(f"Failed to delete chat: {error}")
        st.session_state["current_chat"] = (
            st.session_state["history_chats"][0]["id"] if st.session_state["history_chats"] else None
        )


def select_chat(chat_id):
    st.session_state["current_chat"] = chat_id


with st.sidebar:
    st.title("Chat Management")
    chat_name = st.text_input("Enter Chat Name:", key="new_chat_name")
    if st.button("Create New Chat"):
        if chat_name.strip():
            create_chat(chat_name.strip())
        else:
            st.warning("Chat name cannot be empty.")
    if st.session_state["history_chats"]:
        chat_options = {
            chat["id"]: st.session_state["chat_names"][chat["id"]]
            for chat in st.session_state["history_chats"]
        }
        selected_chat = st.radio(
            "Select Chat",
            options=list(chat_options.keys()),
            format_func=lambda x: chat_options[x],
            key="chat_selector",
            on_change=lambda: select_chat(st.session_state.chat_selector),
        )
        st.session_state["current_chat"] = selected_chat
        st.button("Delete Chat", on_click=delete_chat)


if st.session_state["current_chat"]:
    chat_id = st.session_state["current_chat"]
    chat_name = st.session_state["chat_names"][chat_id]

    current_chat = next(
        (chat for chat in st.session_state["history_chats"] if chat["id"] == chat_id),
        None,
    )

    if current_chat:
        for message in current_chat["messages"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # stage 3: stream the assistant's reply into the active chat
        if prompt := st.chat_input("Your Message:"):
            current_chat["messages"].append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                payload = {
                    "messages": [
                        {"role": m["role"], "content": m["content"]}
                        for m in current_chat["messages"]
                    ]
                }

                def get_stream_response():
                    with requests.post(f"{BACKEND_URL}/chat/", json=payload, stream=True) as r:
                        r.raise_for_status()
                        for chunk in r:
                            yield chunk.decode("utf-8")

                try:
                    response = st.write_stream(get_stream_response)
                except requests.RequestException as error:
                    st.error(f"Backend request failed: {error}")
                    current_chat["messages"].pop()
                else:
                    current_chat["messages"].append({"role": "assistant", "content": response})
                    save_chat_to_db(chat_id, chat_name, current_chat["messages"])
else:
    st.write("No chat selected. Use the sidebar to create or select a chat.")