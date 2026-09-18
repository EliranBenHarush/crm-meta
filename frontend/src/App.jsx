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

const QUICK_REPLIES = [
  "היי, תודה שפנית אלינו 👋",
  "אני בודק וחוזר אליך בהקדם.",
  "מצרף לך פרטים ומחיר.",
  "אפשר בבקשה שם מלא וטלפון?",
  "האספקה היא עד 7 ימי עסקים.",
];

function App() {
  const [conversations, setConversations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [mediaFile, setMediaFile] = useState(null);
  const [mediaSending, setMediaSending] = useState(false);
  const [messageMenuId, setMessageMenuId] = useState(null);
  const [editingMessage, setEditingMessage] = useState(null);
  const [replyToMessage, setReplyToMessage] = useState(null);
  const fileInputRef = useRef(null);
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
  const [view, setView] = useState("chat");
  const [templates, setTemplates] = useState([]);
  const [audience, setAudience] = useState([]);
  const [broadcastHistory, setBroadcastHistory] = useState([]);
  const [selectedTemplateKey, setSelectedTemplateKey] = useState("");
  const [broadcastSearch, setBroadcastSearch] = useState("");
  const [broadcastStatus, setBroadcastStatus] = useState("");
  const [broadcastAssignee, setBroadcastAssignee] = useState("");
  const [broadcastTag, setBroadcastTag] = useState("");
  const [selectedContactIds, setSelectedContactIds] = useState([]);
  const [bodyParameters, setBodyParameters] = useState([]);
  const [broadcastSending, setBroadcastSending] = useState(false);
  const [broadcastReport, setBroadcastReport] = useState(null);
  const [consentConfirmed, setConsentConfirmed] = useState(false);
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
          payload.type !== "contact_crm_updated" &&
          payload.type !== "message_deleted" &&
          payload.type !== "message_status"
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

          if (
            payload.type === "message_deleted" ||
            payload.type === "message_status"
          ) {
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

  const totalUnread = useMemo(
    () =>
      conversations.reduce(
        (total, conversation) =>
          total + (conversation.unread_count || 0),
        0
      ),
    [conversations]
  );

  const selectedTemplate = useMemo(
    () =>
      templates.find(
        (template) =>
          `${template.name}|${template.language}` === selectedTemplateKey
      ) || null,
    [templates, selectedTemplateKey]
  );

  const availableBroadcastTags = useMemo(
    () =>
      Array.from(
        new Set(
          audience.flatMap((contact) => contact.tags || [])
        )
      ).sort(),
    [audience]
  );

  const filteredAudience = useMemo(() => {
    const query = broadcastSearch.trim().toLowerCase();

    return audience.filter((contact) => {
      const matchesSearch =
        !query ||
        (contact.name || "").toLowerCase().includes(query) ||
        (contact.phone || "").includes(query);

      const matchesStatus =
        !broadcastStatus || contact.status === broadcastStatus;

      const matchesAssignee =
        !broadcastAssignee ||
        (contact.assignee || "") === broadcastAssignee;

      const matchesTag =
        !broadcastTag ||
        (contact.tags || []).includes(broadcastTag);

      return (
        matchesSearch &&
        matchesStatus &&
        matchesAssignee &&
        matchesTag
      );
    });
  }, [
    audience,
    broadcastSearch,
    broadcastStatus,
    broadcastAssignee,
    broadcastTag,
  ]);

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
    setMessageMenuId(null);
    setEditingMessage(null);
    setReplyToMessage(null);
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

  async function openBroadcast() {
    setView("broadcast");
    setBroadcastReport(null);

    const [templatesRes, audienceRes, historyRes] = await Promise.all([
      fetch(`${API}/broadcast/templates`),
      fetch(`${API}/broadcast/audience`),
      fetch(`${API}/broadcast/history`),
    ]);

    if (templatesRes.ok) {
      const data = await templatesRes.json();
      setTemplates(data);

      if (data.length > 0 && !selectedTemplateKey) {
        const firstKey = `${data[0].name}|${data[0].language}`;
        setSelectedTemplateKey(firstKey);
        setBodyParameters(
          Array(data[0].body_parameter_count || 0).fill("")
        );
      }
    }

    if (audienceRes.ok) {
      setAudience(await audienceRes.json());
    }

    if (historyRes.ok) {
      setBroadcastHistory(await historyRes.json());
    }
  }

  function chooseTemplate(key) {
    setSelectedTemplateKey(key);

    const template = templates.find(
      (item) => `${item.name}|${item.language}` === key
    );

    setBodyParameters(
      Array(template?.body_parameter_count || 0).fill("")
    );
    setBroadcastReport(null);
  }

  function toggleBroadcastContact(contactId) {
    setSelectedContactIds((current) =>
      current.includes(contactId)
        ? current.filter((id) => id !== contactId)
        : [...current, contactId]
    );
  }

  function selectFilteredAudience() {
    const ids = filteredAudience.map((contact) => contact.id);

    const allSelected =
      ids.length > 0 &&
      ids.every((id) => selectedContactIds.includes(id));

    if (allSelected) {
      setSelectedContactIds((current) =>
        current.filter((id) => !ids.includes(id))
      );
    } else {
      setSelectedContactIds((current) =>
        Array.from(new Set([...current, ...ids]))
      );
    }
  }

  async function sendBroadcast() {
    if (
      !selectedTemplate ||
      selectedContactIds.length === 0 ||
      broadcastSending ||
      !consentConfirmed
    ) {
      return;
    }

    const approved = window.confirm(
      `לשלוח את התבנית "${selectedTemplate.name}" ל-${selectedContactIds.length} לקוחות?`
    );

    if (!approved) return;

    setBroadcastSending(true);
    setBroadcastReport(null);

    try {
      const res = await fetch(`${API}/broadcast/send`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          template_name: selectedTemplate.name,
          language: selectedTemplate.language,
          contact_ids: selectedContactIds,
          body_parameters: bodyParameters,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        alert(JSON.stringify(data));
        return;
      }

      setBroadcastReport(data);
      setSelectedContactIds([]);

      const historyRes = await fetch(`${API}/broadcast/history`);

      if (historyRes.ok) {
        setBroadcastHistory(await historyRes.json());
      }
    } finally {
      setBroadcastSending(false);
    }
  }

  function pickMedia(event) {
    const file = event.target.files?.[0] || null;
    setMediaFile(file);
  }

  function clearMedia() {
    setMediaFile(null);

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  async function sendMedia() {
    const current = selectedRef.current;

    if (!current || !mediaFile || mediaSending) return;

    setMediaSending(true);

    try {
      const form = new FormData();
      form.append("phone", current.phone);
      form.append("file", mediaFile);
      form.append("caption", text.trim());

      const res = await fetch(`${API}/send-media`, {
        method: "POST",
        body: form,
      });

      const data = await res.json();

      if (!res.ok) {
        alert(JSON.stringify(data));
        return;
      }

      setText("");
      clearMedia();
      await loadMessages(current.phone);
      await loadConversations(false);
    } finally {
      setMediaSending(false);
    }
  }

  function messagePreview(message) {
    const value =
      message.body ||
      message.media_filename ||
      (message.type === "image"
        ? "תמונה"
        : message.type === "video"
        ? "וידאו"
        : message.type === "audio"
        ? "אודיו"
        : message.type === "document"
        ? "מסמך"
        : "הודעה");

    return value.length > 80 ? `${value.slice(0, 80)}…` : value;
  }

  function startReply(message) {
    setReplyToMessage(message);
    setEditingMessage(null);
    setMessageMenuId(null);
  }

  function startEdit(message) {
    if (message.direction !== "outgoing" || message.type !== "text") return;

    setEditingMessage(message);
    setReplyToMessage(null);
    setText(message.body || "");
    setMessageMenuId(null);
  }

  async function copyMessage(message) {
    const value = message.body || message.media_filename || "";

    if (!value) return;

    try {
      await navigator.clipboard.writeText(value);
    } catch {
      window.prompt("העתק את הטקסט:", value);
    }

    setMessageMenuId(null);
  }

  async function deleteMessageFromCrm(message) {
    if (message.direction !== "outgoing") return;

    const approved = window.confirm(
      "למחוק את ההודעה מה-CRM?\n\nהיא לא תימחק מה-WhatsApp של הלקוח."
    );

    if (!approved) return;

    const res = await fetch(`${API}/messages/${message.id}`, {
      method: "DELETE",
    });

    const data = await res.json();

    if (!res.ok) {
      alert(JSON.stringify(data));
      return;
    }

    setMessageMenuId(null);

    if (editingMessage?.id === message.id) {
      setEditingMessage(null);
      setText("");
    }

    if (replyToMessage?.id === message.id) {
      setReplyToMessage(null);
    }

    if (selectedRef.current) {
      await loadMessages(selectedRef.current.phone);
      await loadConversations(false);
    }
  }

  async function sendMessage() {
    if (mediaFile) {
      await sendMedia();
      return;
    }

    if (!selectedRef.current || !text.trim()) return;

    const current = selectedRef.current;
    let messageText = text.trim();

    if (editingMessage) {
      messageText = `✏️ תיקון להודעה קודמת:\n${messageText}`;
    } else if (replyToMessage) {
      messageText =
        `↩️ תגובה ל: "${messagePreview(replyToMessage)}"\n${messageText}`;
    }

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

    setEditingMessage(null);
    setReplyToMessage(null);
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

          <div className="main-nav">
            <button
              className={view === "chat" ? "active" : ""}
              onClick={() => setView("chat")}
            >
              שיחות
            </button>
            <button
              className={view === "broadcast" ? "active" : ""}
              onClick={openBroadcast}
            >
              תפוצה
            </button>
          </div>
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

      {view === "chat" ? (
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
                  <button
                    className="message-menu-button"
                    type="button"
                    title="פעולות"
                    onClick={(event) => {
                      event.stopPropagation();
                      setMessageMenuId(
                        messageMenuId === m.id ? null : m.id
                      );
                    }}
                  >
                    ⋮
                  </button>

                  {messageMenuId === m.id && (
                    <div className="message-action-menu">
                      <button type="button" onClick={() => startReply(m)}>
                        השב
                      </button>
                      <button type="button" onClick={() => copyMessage(m)}>
                        העתק
                      </button>
                      {m.direction === "outgoing" && m.type === "text" && (
                        <button type="button" onClick={() => startEdit(m)}>
                          ערוך / תקן
                        </button>
                      )}
                      {m.direction === "outgoing" && (
                        <button
                          type="button"
                          className="danger"
                          onClick={() => deleteMessageFromCrm(m)}
                        >
                          מחק מה-CRM
                        </button>
                      )}
                    </div>
                  )}

                  <div className="message-content">
                    {m.type === "image" && m.media_id && (
                      <a
                        href={`${API}/media/${m.media_id}`}
                        target="_blank"
                        rel="noreferrer"
                        className="media-image-link"
                      >
                        <img
                          className="chat-media-image"
                          src={`${API}/media/${m.media_id}`}
                          alt={m.media_filename || "תמונה"}
                        />
                      </a>
                    )}

                    {m.type === "video" && m.media_id && (
                      <video
                        className="chat-media-video"
                        src={`${API}/media/${m.media_id}`}
                        controls
                        preload="metadata"
                      />
                    )}

                    {m.type === "audio" && m.media_id && (
                      <audio
                        className="chat-media-audio"
                        src={`${API}/media/${m.media_id}`}
                        controls
                        preload="metadata"
                      />
                    )}

                    {m.type === "document" && m.media_id && (
                      <a
                        className="chat-media-document"
                        href={`${API}/media/${m.media_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        📄 {m.media_filename || m.body || "פתח מסמך"}
                      </a>
                    )}

                    {m.type !== "text" &&
                      !m.media_id &&
                      m.type !== "template" && (
                        <div className="media-label">
                          {m.type === "image" && "📷 תמונה"}
                          {m.type === "video" && "🎬 וידאו"}
                          {m.type === "audio" && "🎵 אודיו"}
                          {m.type === "document" && "📄 מסמך"}
                        </div>
                      )}

                    {m.type === "template" && (
                      <div className="media-label">📝 תבנית</div>
                    )}

                    {m.body &&
                      !(m.type === "document" && m.media_id) && (
                        <div className="media-caption">{m.body}</div>
                      )}

                    {!m.body && m.type === "text" && <div>[הודעה]</div>}
                  </div>

                  <small className="message-meta">
                    <span>
                      {new Date(m.created_at).toLocaleTimeString(
                        "he-IL",
                        {
                          hour: "2-digit",
                          minute: "2-digit",
                        }
                      )}
                    </span>
                    {m.direction === "outgoing" && (
                      <span
                        className={[
                          "delivery-check",
                          m.delivery_status === "read" ? "read" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        title={
                          m.delivery_status === "read"
                            ? "נקרא"
                            : m.delivery_status === "delivered"
                            ? "נמסר"
                            : m.delivery_status === "sent"
                            ? "נשלח"
                            : "התקבל ב-WhatsApp"
                        }
                      >
                        {m.delivery_status === "read" ||
                        m.delivery_status === "delivered"
                          ? "✓✓"
                          : "✓"}
                      </span>
                    )}
                  </small>
                </div>
              ))}
            </div>

            {(editingMessage || replyToMessage) && (
              <div className="message-context-bar">
                <div>
                  <strong>
                    {editingMessage ? "✏️ תיקון הודעה" : "↩️ תגובה להודעה"}
                  </strong>
                  <span>
                    {messagePreview(editingMessage || replyToMessage)}
                  </span>
                  {editingMessage && (
                    <small>
                      WhatsApp לא מאפשר עריכה דרך ה-API — תישלח הודעת תיקון חדשה.
                    </small>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setEditingMessage(null);
                    setReplyToMessage(null);
                    if (editingMessage) setText("");
                  }}
                  title="בטל"
                >
                  ×
                </button>
              </div>
            )}

            {mediaFile && (
              <div className="media-preview">
                <div>
                  <strong>קובץ מצורף</strong>
                  <span>{mediaFile.name}</span>
                </div>
                <button type="button" onClick={clearMedia} title="הסר קובץ">
                  ×
                </button>
              </div>
            )}

            <div className="quick-replies">
              {QUICK_REPLIES.map((reply) => (
                <button
                  key={reply}
                  type="button"
                  onClick={() => {
                    setEditingMessage(null);
                    setReplyToMessage(null);
                    setText(reply);
                  }}
                  title={reply}
                >
                  {reply}
                </button>
              ))}
            </div>

            <div className="composer">
              <input
                ref={fileInputRef}
                className="media-file-input"
                type="file"
                accept="image/*,video/*,audio/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip"
                onChange={pickMedia}
              />

              <button
                type="button"
                className="attach-button"
                onClick={() => fileInputRef.current?.click()}
                disabled={mediaSending}
                title="צרף תמונה, וידאו, אודיו או מסמך"
              >
                📎
              </button>

              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !mediaSending) sendMessage();
                }}
                placeholder={
                  mediaFile
                    ? "הוסף כיתוב לקובץ (לא חובה)..."
                    : editingMessage
                    ? "כתוב את התיקון..."
                    : replyToMessage
                    ? "כתוב תגובה..."
                    : "כתוב הודעה..."
                }
              />

              <button
                onClick={sendMessage}
                disabled={mediaSending || (!mediaFile && !text.trim())}
              >
                {mediaSending ? "שולח..." : "שלח"}
              </button>
            </div>
          </>
        ) : (
          <div className="empty-chat">
            בחר שיחה כדי להתחיל
          </div>
        )}
      </main>
      ) : (
        <main className="broadcast-page">
          <div className="broadcast-header">
            <div>
              <h2>שליחת תפוצה ב-WhatsApp</h2>
              <p>
                בחר תבנית מאושרת של Meta, סנן לקוחות ושלח.
              </p>
            </div>
            <button className="refresh-button" onClick={openBroadcast}>
              רענן תבניות
            </button>
          </div>

          <div className="broadcast-grid">
            <section className="broadcast-card">
              <h3>1. תבנית</h3>

              <select
                className="broadcast-select"
                value={selectedTemplateKey}
                onChange={(e) => chooseTemplate(e.target.value)}
              >
                {templates.length === 0 && (
                  <option value="">לא נמצאו תבניות מאושרות</option>
                )}
                {templates.map((template) => {
                  const key = `${template.name}|${template.language}`;

                  return (
                    <option key={key} value={key}>
                      {template.name} · {template.language} · {template.category}
                    </option>
                  );
                })}
              </select>

              {selectedTemplate && (
                <>
                  <div className="template-preview">
                    <strong>{selectedTemplate.name}</strong>
                    <div>
                      {selectedTemplate.body_text || "אין טקסט BODY"}
                    </div>
                  </div>

                  {bodyParameters.map((value, index) => (
                    <input
                      key={index}
                      className="broadcast-input"
                      value={value}
                      onChange={(e) => {
                        const next = [...bodyParameters];
                        next[index] = e.target.value;
                        setBodyParameters(next);
                      }}
                      placeholder={
                        index === 0
                          ? `ערך ל-{{${index + 1}}} — אפשר {name}`
                          : `ערך ל-{{${index + 1}}}`
                      }
                    />
                  ))}
                </>
              )}
            </section>

            <section className="broadcast-card">
              <h3>2. קהל</h3>

              <div className="broadcast-filters">
                <input
                  className="broadcast-input"
                  value={broadcastSearch}
                  onChange={(e) => setBroadcastSearch(e.target.value)}
                  placeholder="חיפוש שם או טלפון"
                />

                <select
                  className="broadcast-select"
                  value={broadcastStatus}
                  onChange={(e) => setBroadcastStatus(e.target.value)}
                >
                  <option value="">כל הסטטוסים</option>
                  {STATUS_OPTIONS.map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>

                <select
                  className="broadcast-select"
                  value={broadcastAssignee}
                  onChange={(e) => setBroadcastAssignee(e.target.value)}
                >
                  <option value="">כל הנציגים</option>
                  <option value="אלירן">אלירן</option>
                  <option value="אורן">אורן</option>
                </select>

                <select
                  className="broadcast-select"
                  value={broadcastTag}
                  onChange={(e) => setBroadcastTag(e.target.value)}
                >
                  <option value="">כל התגיות</option>
                  {availableBroadcastTags.map((tag) => (
                    <option key={tag} value={tag}>
                      {tag}
                    </option>
                  ))}
                </select>
              </div>

              <div className="audience-toolbar">
                <button onClick={selectFilteredAudience}>
                  בחר/בטל את כל המסוננים
                </button>
                <strong>
                  נבחרו {selectedContactIds.length} מתוך {filteredAudience.length}
                </strong>
              </div>

              <div className="audience-list">
                {filteredAudience.map((contact) => (
                  <label className="audience-row" key={contact.id}>
                    <input
                      type="checkbox"
                      checked={selectedContactIds.includes(contact.id)}
                      onChange={() => toggleBroadcastContact(contact.id)}
                    />
                    <div>
                      <strong>{contact.name || contact.phone}</strong>
                      <span>{contact.phone}</span>
                      <small>
                        {contact.status}
                        {contact.assignee ? ` · ${contact.assignee}` : ""}
                        {(contact.tags || []).length
                          ? ` · ${contact.tags.join(", ")}`
                          : ""}
                      </small>
                    </div>
                  </label>
                ))}
              </div>
            </section>
          </div>

          <section className="broadcast-card send-card">
            <label className="consent-check">
              <input
                type="checkbox"
                checked={consentConfirmed}
                onChange={(e) => setConsentConfirmed(e.target.checked)}
              />
              <span>
                אני מאשר שהנמענים שנבחרו נתנו הסכמה לקבלת הודעות WhatsApp
                מהעסק.
              </span>
            </label>

            <button
              className="send-broadcast-button"
              onClick={sendBroadcast}
              disabled={
                broadcastSending ||
                !selectedTemplate ||
                selectedContactIds.length === 0 ||
                !consentConfirmed
              }
            >
              {broadcastSending
                ? "שולח..."
                : `שלח תפוצה ל-${selectedContactIds.length} לקוחות`}
            </button>
          </section>

          {broadcastReport && (
            <section className="broadcast-card">
              <h3>דוח שליחה</h3>
              <div className="broadcast-summary">
                <strong>סה״כ: {broadcastReport.audience_count}</strong>
                <strong>נשלחו: {broadcastReport.success_count}</strong>
                <strong>נכשלו: {broadcastReport.failed_count}</strong>
              </div>

              <div className="delivery-list">
                {broadcastReport.deliveries.map((delivery) => (
                  <div
                    key={delivery.contact_id}
                    className={`delivery-row ${delivery.status}`}
                  >
                    <div className="delivery-info">
                      <span>{delivery.name || delivery.phone}</span>
                      {delivery.status === "failed" && delivery.error && (
                        <small className="delivery-error">
                          {delivery.error?.error?.message ||
                            delivery.error?.message ||
                            JSON.stringify(delivery.error)}
                        </small>
                      )}
                    </div>
                    <strong>
                      {delivery.status === "sent" ? "נשלח ✓" : "נכשל"}
                    </strong>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="broadcast-card">
            <h3>שליחות אחרונות</h3>
            {broadcastHistory.length === 0 ? (
              <p className="empty-crm-text">עדיין אין שליחות.</p>
            ) : (
              <div className="history-list">
                {broadcastHistory.map((run) => (
                  <div className="history-row" key={run.id}>
                    <div>
                      <strong>{run.template_name}</strong>
                      <small>
                        {new Date(run.created_at).toLocaleString("he-IL")}
                      </small>
                    </div>
                    <span>
                      {run.success_count}/{run.audience_count} נשלחו
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </main>
      )}

      {view === "chat" ? (
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
      ) : (
        <aside className="customer-panel broadcast-side">
          <h3>תפוצה</h3>
          <p>
            התבניות נטענות ישירות מחשבון WhatsApp Business המחובר ל-CRM.
          </p>
          <p>
            ניתן להשתמש ב-{"{name}"} או {"{phone}"} בתוך ערכי המשתנים.
          </p>
        </aside>
      )}
    </div>
  );
}

export default App;
