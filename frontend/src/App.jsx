import { useEffect, useRef, useState } from "react";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

function App() {
  const [conversations, setConversations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const selectedRef = useRef(null);

  useEffect(() => {
    loadConversations();

    const events = new EventSource(`${API}/events`);

    events.onmessage = async (event) => {
      try {
        const payload = JSON.parse(event.data);

        if (payload.type !== "new_message") return;

        await loadConversations(false);

        const current = selectedRef.current;

        if (current?.phone === payload.phone) {
          await loadMessages(payload.phone);
        }
      } catch (error) {
        console.error("SSE event error:", error);
      }
    };

    events.onerror = (error) => {
      console.error("SSE connection error:", error);
      // EventSource reconnects automatically.
    };

    return () => {
      events.close();
    };
  }, []);

  async function loadConversations(autoSelect = true) {
    const res = await fetch(`${API}/conversations`);
    const data = await res.json();
    setConversations(data);

    if (data.length > 0 && !selectedRef.current && autoSelect) {
      await selectConversation(data[0]);
    }
  }

  async function loadMessages(phone) {
    const res = await fetch(`${API}/messages/${phone}`);
    const data = await res.json();
    setMessages(data);
  }

  async function selectConversation(conversation) {
    selectedRef.current = conversation;
    setSelected(conversation);
    await loadMessages(conversation.phone);
  }

  async function sendMessage() {
    if (!selectedRef.current || !text.trim()) return;

    const messageText = text;
    const current = selectedRef.current;
    setText("");

    const res = await fetch(`${API}/send-message`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        phone: current.phone,
        text: messageText,
      }),
    });

    if (!res.ok) {
      const error = await res.json();
      alert(JSON.stringify(error));
      return;
    }

    // SSE will normally refresh this immediately. These calls also make
    // sending feel responsive if the SSE reconnects at the same moment.
    await loadMessages(current.phone);
    await loadConversations(false);
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
