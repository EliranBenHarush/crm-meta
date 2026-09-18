import { useEffect, useState } from "react";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

function App() {
  const [conversations, setConversations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");

  useEffect(() => {
    loadConversations();
  }, []);

  async function loadConversations() {
    const res = await fetch(`${API}/conversations`);
    const data = await res.json();
    setConversations(data);

    if (data.length > 0 && !selected) {
      selectConversation(data[0]);
    }
  }

  async function selectConversation(conversation) {
    setSelected(conversation);

    const res = await fetch(
      `${API}/messages/${conversation.phone}`
    );

    const data = await res.json();
    setMessages(data);
  }

  async function sendMessage() {
    if (!selected || !text.trim()) return;

    const messageText = text;
    setText("");

    const res = await fetch(`${API}/send-message`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        phone: selected.phone,
        text: messageText,
      }),
    });

    if (!res.ok) {
      const error = await res.json();
      alert(JSON.stringify(error));
      return;
    }

    await selectConversation(selected);
    await loadConversations();
  }

  return (
    <div className="crm" dir="rtl">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>Arcadia CRM</h2>
          <span>WhatsApp</span>
        </div>

        <div className="search">
          <input placeholder="חיפוש לקוח..." />
        </div>

        <div className="conversation-list">
          {conversations.map((c) => (
            <button
              key={c.id}
              className={
                selected?.id === c.id
                  ? "conversation active"
                  : "conversation"
              }
              onClick={() => selectConversation(c)}
            >
              <div className="avatar">
                {(c.name || "?").charAt(0)}
              </div>

              <div className="conversation-info">
                <div className="conversation-top">
                  <strong>{c.name || c.phone}</strong>
                  <small>
                    {c.updated_at
                      ? new Date(c.updated_at).toLocaleTimeString(
                          "he-IL",
                          {
                            hour: "2-digit",
                            minute: "2-digit",
                          }
                        )
                      : ""}
                  </small>
                </div>

                <div className="last-message">
                  {c.last_message || "אין הודעות"}
                </div>
              </div>
            </button>
          ))}
        </div>
      </aside>

      <main className="chat">
        {selected ? (
          <>
            <div className="chat-header">
              <div className="avatar large">
                {(selected.name || "?").charAt(0)}
              </div>

              <div>
                <h3>{selected.name || selected.phone}</h3>
                <span>{selected.phone}</span>
              </div>
            </div>

            <div className="messages">
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={
                    m.direction === "outgoing"
                      ? "message outgoing"
                      : "message incoming"
                  }
                >
                  <div>{m.body || `[${m.type}]`}</div>

                  <small>
                    {new Date(m.created_at).toLocaleTimeString(
                      "he-IL",
                      {
                        hour: "2-digit",
                        minute: "2-digit",
                      }
                    )}
                  </small>
                </div>
              ))}
            </div>

            <div className="composer">
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") sendMessage();
                }}
                placeholder="כתוב הודעה..."
              />

              <button onClick={sendMessage}>שלח</button>
            </div>
          </>
        ) : (
          <div className="empty-chat">
            בחר שיחה כדי להתחיל
          </div>
        )}
      </main>

      <aside className="customer-panel">
        {selected && (
          <>
            <div className="customer-avatar">
              {(selected.name || "?").charAt(0)}
            </div>

            <h3>{selected.name || "לקוח"}</h3>
            <p>{selected.phone}</p>

            <div className="customer-card">
              <span>סטטוס</span>
              <strong>{selected.status || "ליד חדש"}</strong>
            </div>

            <div className="customer-card">
              <span>WhatsApp</span>
              <strong>מחובר</strong>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

export default App;