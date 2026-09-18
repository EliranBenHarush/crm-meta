import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

const STATUS_OPTIONS = [
  "ליד חדש",
  "בטיפול",
  "הצעת מחיר",
  "נסגר",
  "לא רלוונטי",
];

function App() {
  const [conversations, setConversations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [statusSaving, setStatusSaving] = useState(false);
  const selectedRef = useRef(null);

  useEffect(() => {
    loadConversations();

    const events = new EventSource(`${API}/events`);

    events.onmessage = async (event) => {
      try {
        const payload = JSON.parse(event.data);

        if (
          payload.type !== "new_message" &&
          payload.type !== "contact_updated"
        ) {
          return;
        }

        await loadConversations(false);

        const current = selectedRef.current;

        if (current?.phone === payload.phone) {
          if (payload.type === "new_message") {
            await loadMessages(payload.phone);
          }

          if (payload.type === "contact_updated") {
            const updated = {
              ...current,
              status: payload.status,
              updated_at: payload.updated_at,
            };

            selectedRef.current = updated;
            setSelected(updated);
          }
        }
      } catch (error) {
        console.error("SSE event error:", error);
      }
    };

    events.onerror = (error) => {
      console.error("SSE connection error:", error);
    };

    return () => {
      events.close();
    };
  }, []);

  const filteredConversations = useMemo(() => {
    const query = search.trim().toLowerCase();

    if (!query) return conversations;

    return conversations.filter((conversation) => {
      const name = (conversation.name || "").toLowerCase();
      const phone = (conversation.phone || "").toLowerCase();
      const status = (conversation.status || "").toLowerCase();
      const lastMessage = (conversation.last_message || "").toLowerCase();

      return (
        name.includes(query) ||
        phone.includes(query) ||
        status.includes(query) ||
        lastMessage.includes(query)
      );
    });
  }, [conversations, search]);

  async function loadConversations(autoSelect = true) {
    const res = await fetch(`${API}/conversations`);
    const data = await res.json();
    setConversations(data);

    const current = selectedRef.current;

    if (current) {
      const refreshed = data.find((item) => item.phone === current.phone);

      if (refreshed) {
        selectedRef.current = refreshed;
        setSelected(refreshed);
      }
    } else if (data.length > 0 && autoSelect) {
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

  async function updateStatus(status) {
    const current = selectedRef.current;

    if (!current || statusSaving) return;

    setStatusSaving(true);

    try {
      const res = await fetch(
        `${API}/contacts/${current.phone}/status`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ status }),
        }
      );

      if (!res.ok) {
        const error = await res.json();
        alert(JSON.stringify(error));
        return;
      }

      const updated = { ...current, status };
      selectedRef.current = updated;
      setSelected(updated);
      await loadConversations(false);
    } finally {
      setStatusSaving(false);
    }
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
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="חיפוש שם, טלפון, סטטוס או הודעה..."
          />
        </div>

        <div className="conversation-list">
          {filteredConversations.length === 0 ? (
            <div className="no-results">לא נמצאו לקוחות</div>
          ) : (
            filteredConversations.map((c) => (
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

                  <div className="conversation-meta">
                    <span className="status-pill">
                      {c.status || "ליד חדש"}
                    </span>
                  </div>

                  <div className="last-message">
                    {c.last_message || "אין הודעות"}
                  </div>
                </div>
              </button>
            ))
          )}
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
              <select
                className="status-select"
                value={selected.status || "ליד חדש"}
                onChange={(e) => updateStatus(e.target.value)}
                disabled={statusSaving}
              >
                {STATUS_OPTIONS.map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
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
