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

const ASSIGNEE_OPTIONS = ["", "אלירן", "אורן"];

function App() {
  const [conversations, setConversations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [statusSaving, setStatusSaving] = useState(false);
  const [notes, setNotes] = useState([]);
  const [tags, setTags] = useState([]);
  const [noteText, setNoteText] = useState("");
  const [tagText, setTagText] = useState("");
  const [crmSaving, setCrmSaving] = useState(false);
  const [assignee, setAssignee] = useState("");
  const [reminders, setReminders] = useState([]);
  const [reminderText, setReminderText] = useState("");
  const [reminderAt, setReminderAt] = useState("");
  const selectedRef = useRef(null);

  useEffect(() => {
    loadConversations();

    const events = new EventSource(`${API}/events`);

    events.onmessage = async (event) => {
      try {
        const payload = JSON.parse(event.data);

        if (
          payload.type !== "new_message" &&
          payload.type !== "contact_updated" &&
          payload.type !== "conversation_read" &&
          payload.type !== "contact_crm_updated"
        ) {
          return;
        }

        await loadConversations(false);

        const current = selectedRef.current;

        if (current?.phone === payload.phone) {
          if (payload.type === "new_message") {
            await loadMessages(payload.phone);

            if (payload.message?.direction === "incoming") {
              await markConversationRead(payload.phone);
            }
          }

          if (payload.type === "contact_crm_updated") {
            await loadContactCrm(payload.phone);
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

  const totalUnread = useMemo(
    () =>
      conversations.reduce(
        (total, conversation) =>
          total + (conversation.unread_count || 0),
        0
      ),
    [conversations]
  );

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

  async function loadContactCrm(phone) {
    const res = await fetch(`${API}/contacts/${phone}/crm`);

    if (!res.ok) return;

    const data = await res.json();
    setNotes(data.notes || []);
    setTags(data.tags || []);
    setAssignee(data.assignee || "");
    setReminders(data.reminders || []);
  }

  async function selectConversation(conversation) {
    selectedRef.current = conversation;
    setSelected(conversation);
    await Promise.all([
      loadMessages(conversation.phone),
      loadContactCrm(conversation.phone),
    ]);

    if ((conversation.unread_count || 0) > 0) {
      await markConversationRead(conversation.phone);
    }
  }

  async function markConversationRead(phone) {
    const res = await fetch(`${API}/contacts/${phone}/read`, {
      method: "POST",
    });

    if (!res.ok) return;

    setConversations((current) =>
      current.map((conversation) =>
        conversation.phone === phone
          ? { ...conversation, unread_count: 0 }
          : conversation
      )
    );

    if (selectedRef.current?.phone === phone) {
      const updated = {
        ...selectedRef.current,
        unread_count: 0,
      };

      selectedRef.current = updated;
      setSelected(updated);
    }
  }

  async function addNote() {
    const current = selectedRef.current;
    const body = noteText.trim();

    if (!current || !body || crmSaving) return;

    setCrmSaving(true);

    try {
      const res = await fetch(
        `${API}/contacts/${current.phone}/notes`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ body }),
        }
      );

      if (!res.ok) {
        const error = await res.json();
        alert(JSON.stringify(error));
        return;
      }

      setNoteText("");
      await loadContactCrm(current.phone);
    } finally {
      setCrmSaving(false);
    }
  }

  async function addTag() {
    const current = selectedRef.current;
    const name = tagText.trim();

    if (!current || !name || crmSaving) return;

    setCrmSaving(true);

    try {
      const res = await fetch(
        `${API}/contacts/${current.phone}/tags`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ name }),
        }
      );

      if (!res.ok) {
        const error = await res.json();
        alert(JSON.stringify(error));
        return;
      }

      setTagText("");
      await loadContactCrm(current.phone);
    } finally {
      setCrmSaving(false);
    }
  }

  async function deleteTag(tagId) {
    const current = selectedRef.current;

    if (!current || crmSaving) return;

    setCrmSaving(true);

    try {
      const res = await fetch(
        `${API}/contacts/${current.phone}/tags/${tagId}`,
        { method: "DELETE" }
      );

      if (!res.ok) return;

      await loadContactCrm(current.phone);
    } finally {
      setCrmSaving(false);
    }
  }

  async function updateAssignee(value) {
    const current = selectedRef.current;

    if (!current || crmSaving) return;

    setCrmSaving(true);

    try {
      const res = await fetch(
        `${API}/contacts/${current.phone}/assignee`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ assignee: value }),
        }
      );

      if (!res.ok) {
        const error = await res.json();
        alert(JSON.stringify(error));
        return;
      }

      setAssignee(value);
    } finally {
      setCrmSaving(false);
    }
  }

  async function addReminder() {
    const current = selectedRef.current;
    const note = reminderText.trim();

    if (!current || !note || !reminderAt || crmSaving) return;

    setCrmSaving(true);

    try {
      const res = await fetch(
        `${API}/contacts/${current.phone}/reminders`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            note,
            due_at: reminderAt,
          }),
        }
      );

      if (!res.ok) {
        const error = await res.json();
        alert(JSON.stringify(error));
        return;
      }

      setReminderText("");
      setReminderAt("");
      await loadContactCrm(current.phone);
    } finally {
      setCrmSaving(false);
    }
  }

  async function completeReminder(reminderId) {
    const current = selectedRef.current;

    if (!current || crmSaving) return;

    setCrmSaving(true);

    try {
      const res = await fetch(
        `${API}/contacts/${current.phone}/reminders/${reminderId}/complete`,
        { method: "PATCH" }
      );

      if (!res.ok) return;

      await loadContactCrm(current.phone);
    } finally {
      setCrmSaving(false);
    }
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
          <div className="sidebar-title-row">
            <h2>Arcadia CRM</h2>
            {totalUnread > 0 && (
              <span className="total-unread">{totalUnread}</span>
            )}
          </div>
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
                className={[
                  "conversation",
                  selected?.id === c.id ? "active" : "",
                  (c.unread_count || 0) > 0 ? "unread" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onClick={() => selectConversation(c)}
              >
                <div className="avatar">
                  {(c.name || "?").charAt(0)}
                </div>

                <div className="conversation-info">
                  <div className="conversation-top">
                    <strong>{c.name || c.phone}</strong>
                    <div className="conversation-top-side">
                      {(c.unread_count || 0) > 0 && (
                        <span className="unread-badge">
                          {c.unread_count}
                        </span>
                      )}
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
              <span>נציג מטפל</span>
              <select
                className="status-select"
                value={assignee}
                onChange={(e) => updateAssignee(e.target.value)}
                disabled={crmSaving}
              >
                {ASSIGNEE_OPTIONS.map((name) => (
                  <option key={name || "none"} value={name}>
                    {name || "לא משויך"}
                  </option>
                ))}
              </select>
            </div>

            <div className="customer-card">
              <span>תזכורת / פולואפ</span>

              <input
                className="reminder-datetime"
                type="datetime-local"
                value={reminderAt}
                onChange={(e) => setReminderAt(e.target.value)}
              />

              <textarea
                className="note-input reminder-note"
                value={reminderText}
                onChange={(e) => setReminderText(e.target.value)}
                placeholder="למשל: לחזור ללקוח לגבי הצעת המחיר"
                maxLength={500}
              />

              <button
                className="save-note-button"
                onClick={addReminder}
                disabled={
                  crmSaving ||
                  !reminderText.trim() ||
                  !reminderAt
                }
              >
                הוסף תזכורת
              </button>

              <div className="reminders-list">
                {reminders.length === 0 ? (
                  <small className="empty-crm-text">
                    אין תזכורות עדיין
                  </small>
                ) : (
                  reminders.map((reminder) => {
                    const overdue =
                      !reminder.completed_at &&
                      new Date(reminder.due_at).getTime() < Date.now();

                    return (
                      <div
                        key={reminder.id}
                        className={[
                          "reminder-item",
                          reminder.completed_at ? "completed" : "",
                          overdue ? "overdue" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                      >
                        <div className="reminder-note-text">
                          {reminder.note}
                        </div>

                        <small>
                          {new Date(reminder.due_at).toLocaleString(
                            "he-IL",
                            {
                              day: "2-digit",
                              month: "2-digit",
                              year: "2-digit",
                              hour: "2-digit",
                              minute: "2-digit",
                            }
                          )}
                        </small>

                        {!reminder.completed_at && (
                          <button
                            className="complete-reminder-button"
                            onClick={() =>
                              completeReminder(reminder.id)
                            }
                          >
                            סמן כבוצע
                          </button>
                        )}

                        {reminder.completed_at && (
                          <strong className="reminder-done">
                            ✓ בוצע
                          </strong>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            <div className="customer-card">
              <span>תגיות</span>

              <div className="tag-list">
                {tags.length === 0 ? (
                  <small className="empty-crm-text">אין תגיות עדיין</small>
                ) : (
                  tags.map((tag) => (
                    <button
                      key={tag.id}
                      className="crm-tag"
                      title="לחץ להסרה"
                      onClick={() => deleteTag(tag.id)}
                    >
                      {tag.name} ×
                    </button>
                  ))
                )}
              </div>

              <div className="crm-inline-form">
                <input
                  value={tagText}
                  onChange={(e) => setTagText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addTag();
                  }}
                  placeholder="למשל: לקוח חם"
                  maxLength={50}
                />
                <button onClick={addTag} disabled={crmSaving}>
                  הוסף
                </button>
              </div>
            </div>

            <div className="customer-card">
              <span>הערות פנימיות</span>

              <textarea
                className="note-input"
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder="למשל: מחפש שולחן 3 מטר..."
                maxLength={2000}
              />

              <button
                className="save-note-button"
                onClick={addNote}
                disabled={crmSaving || !noteText.trim()}
              >
                שמור הערה
              </button>

              <div className="notes-list">
                {notes.length === 0 ? (
                  <small className="empty-crm-text">אין הערות עדיין</small>
                ) : (
                  notes.map((note) => (
                    <div className="note-item" key={note.id}>
                      <div>{note.body}</div>
                      <small>
                        {new Date(note.created_at).toLocaleString("he-IL", {
                          day: "2-digit",
                          month: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </small>
                    </div>
                  ))
                )}
              </div>
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
