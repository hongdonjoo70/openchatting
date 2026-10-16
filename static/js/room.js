const chatApp = document.getElementById("chat-app");
const ROOM_ID = parseInt(chatApp.dataset.roomId, 10);
const CURRENT_USER_ID = chatApp.dataset.userId;
const CURRENT_USER_NAME = chatApp.dataset.userName;

const socket = io();

let connectedUsers = [];
let localScreenStream = null;
let screenFrameInterval = null;
let isSharingScreen = false;
let cachedDbData = { notices: [], messages: [] };

function scrollToBottom() {
    const list = document.getElementById("message-list");
    if (list) list.scrollTop = list.scrollHeight;
}
document.addEventListener("DOMContentLoaded", scrollToBottom);

socket.on("connect", () => {
    socket.emit("join_chat_room", { room_id: ROOM_ID });
});

socket.on("auth_error", (data) => {
    alert(data.message || "세션이 만료되었거나 동일 IP에서 다른 계정이 접속 중입니다.");
    window.location.href = "/login";
});

socket.on("user_list_update", (data) => {
    if (data.room_id !== ROOM_ID) return;
    connectedUsers = data.users || [];

    const countBadge = document.getElementById("participant-count-badge");
    if (countBadge) countBadge.textContent = `${data.count}명`;

    const listEl = document.getElementById("participant-list");
    const recipientSelect = document.getElementById("recipient-select");
    const shareTargetSelect = document.getElementById("share-target-select");

    const currentRecipient = recipientSelect.value;
    const currentShareTarget = shareTargetSelect ? shareTargetSelect.value : "";

    listEl.innerHTML = "";
    recipientSelect.innerHTML = `<option value="">🌐 전체 접속자에게 전송 (전체 전송)</option>`;
    if (shareTargetSelect) {
        shareTargetSelect.innerHTML = `<option value="">🌐 채팅방 전체 접속자에게 공유</option>`;
    }

    connectedUsers.forEach((u) => {
        const isMe = u.user_id === CURRENT_USER_ID;
        const li = document.createElement("li");
        li.className = `d-flex align-items-center justify-content-between gap-2 p-2 border rounded-2 small ${isMe ? "bg-primary-subtle border-primary-subtle" : "bg-light"}`;

        const badgeHtml = isMe ? `<span class="badge text-bg-primary">나(Me)</span>` : "";
        const sharingHtml = u.sharing ? `<span class="badge text-bg-danger">🖥️ 창 공유중</span>` : "";

        let actionsHtml = "";
        if (!isMe) {
            actionsHtml = `
                <div class="d-flex gap-1 flex-shrink-0">
                    <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-2"
                            onclick="selectRecipientUser('${escapeAttr(u.user_id)}', '${escapeAttr(u.name)}')">
                        💬 귓속말
                    </button>
                    <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-2"
                            onclick="openScreenShareForUser('${escapeAttr(u.user_id)}')">
                        🖥️ 창 공유
                    </button>
                </div>
            `;

            const opt1 = document.createElement("option");
            opt1.value = u.user_id;
            opt1.dataset.userName = u.name;
            opt1.textContent = `🔒 ${u.name} (${u.user_id})님에게만 전송`;
            recipientSelect.appendChild(opt1);

            if (shareTargetSelect) {
                const opt2 = document.createElement("option");
                opt2.value = u.user_id;
                opt2.dataset.userName = u.name;
                opt2.textContent = `🔒 ${u.name} (${u.user_id})님에게만 화면 공유`;
                shareTargetSelect.appendChild(opt2);
            }
        }

        li.innerHTML = `
            <div class="d-flex align-items-center gap-1 flex-wrap">
                <span class="status-dot"></span>
                <strong>${escapeHtml(u.name)}</strong>
                <span class="text-secondary">(${escapeHtml(u.user_id)})</span>
                ${badgeHtml}
                ${sharingHtml}
            </div>
            ${actionsHtml}
        `;
        listEl.appendChild(li);
    });

    if (currentRecipient && Array.from(recipientSelect.options).some((o) => o.value === currentRecipient)) {
        recipientSelect.value = currentRecipient;
    }
    if (shareTargetSelect && currentShareTarget && Array.from(shareTargetSelect.options).some((o) => o.value === currentShareTarget)) {
        shareTargetSelect.value = currentShareTarget;
    }
    onRecipientChange();
});

function selectRecipientUser(userId) {
    const recipientSelect = document.getElementById("recipient-select");
    recipientSelect.value = userId;
    onRecipientChange();
    document.getElementById("message-input").focus();
}

function resetRecipient() {
    const recipientSelect = document.getElementById("recipient-select");
    recipientSelect.value = "";
    onRecipientChange();
}

function onRecipientChange() {
    const recipientSelect = document.getElementById("recipient-select");
    const indicator = document.getElementById("whisper-indicator");
    const msgInput = document.getElementById("message-input");

    if (recipientSelect.value) {
        const selectedOpt = recipientSelect.options[recipientSelect.selectedIndex];
        const targetName = selectedOpt.dataset.userName || recipientSelect.value;
        indicator.classList.remove("hidden");
        msgInput.placeholder = `🔒 [${targetName}(${recipientSelect.value})]님에게만 보낼 메시지를 입력하세요...`;
    } else {
        indicator.classList.add("hidden");
        msgInput.placeholder = "메시지를 입력하세요... (Enter로 전송)";
    }
}

function sendChatMessage(e) {
    e.preventDefault();
    const input = document.getElementById("message-input");
    const content = input.value.trim();
    if (!content) return;

    const recipientSelect = document.getElementById("recipient-select");
    const targetUserId = recipientSelect.value || null;
    let targetUserName = null;
    if (targetUserId) {
        const opt = recipientSelect.options[recipientSelect.selectedIndex];
        targetUserName = opt ? opt.dataset.userName : targetUserId;
    }

    socket.emit("send_message", {
        room_id: ROOM_ID,
        content: content,
        target_user_id: targetUserId,
        target_user_name: targetUserName,
    });

    input.value = "";
    input.focus();
}

socket.on("new_message", (msg) => {
    if (msg.room_id !== ROOM_ID) return;
    appendMessageToDOM(msg);
    if (msg.msg_type === "file" && msg.file_id) {
        appendFileToSidebar(msg);
    }
    scrollToBottom();
});

function appendMessageToDOM(msg) {
    const list = document.getElementById("message-list");
    if (!list) return;

    if (msg.msg_type === "system") {
        const div = document.createElement("div");
        div.className = "msg-system align-self-center badge rounded-pill text-bg-secondary bg-opacity-25 text-dark fw-normal py-1 px-3";
        const timeShort = (msg.created_at || "").split(" ")[1] || msg.created_at;
        div.innerHTML = `<span>${escapeHtml(msg.content)}</span><time class="text-secondary ms-1">${escapeHtml(timeShort)}</time>`;
        list.appendChild(div);
        return;
    }

    const isMe = msg.sender_id === CURRENT_USER_ID;
    const isWhisper = Boolean(msg.target_user_id);

    const row = document.createElement("div");
    row.className = `msg-row d-flex w-100 ${isMe ? "msg-mine justify-content-end" : "msg-other justify-content-start"} ${isWhisper ? "msg-whisper" : ""}`;

    const scopeBadge = isWhisper
        ? `<span class="whisper-badge badge rounded-pill">🔒 귓속말 → ${escapeHtml(msg.target_user_name || msg.target_user_id)}</span>`
        : `<span class="badge rounded-pill text-bg-light border text-secondary">전체</span>`;

    let bodyHtml = "";
    if (msg.msg_type === "file" && msg.file_id) {
        const kb = ((msg.file_size || 0) / 1024).toFixed(1);
        const downloadUrl = `/download/${msg.file_id}`;
        const isImg = /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(msg.file_name || "");
        const previewHtml = isImg
            ? `<div class="mb-2"><img src="/preview/${msg.file_id}" class="rounded-2 border" style="max-width:240px;max-height:170px;" alt="${escapeAttr(msg.file_name)}" onload="scrollToBottom()"></div>`
            : "";

        bodyHtml = `
            ${previewHtml}
            <div class="d-flex align-items-center justify-content-between gap-3 bg-white bg-opacity-75 border rounded-2 p-2">
                <div class="d-flex align-items-center gap-2">
                    <span>📎</span>
                    <div>
                        <div class="fw-semibold small text-break">${escapeHtml(msg.file_name)}</div>
                        <div class="text-secondary" style="font-size:0.74rem;">${kb} KB</div>
                    </div>
                </div>
                <a href="${downloadUrl}" class="btn btn-primary btn-sm py-0 px-2">⬇️ 다운로드</a>
            </div>
        `;
    } else {
        bodyHtml = `<div class="msg-text">${escapeHtml(msg.content)}</div>`;
    }

    row.innerHTML = `
        <div class="msg-bubble p-2 px-3 rounded-3 border shadow-sm">
            <div class="d-flex align-items-center gap-2 mb-1 flex-wrap small">
                <strong>${escapeHtml(msg.sender_name)} (${escapeHtml(msg.sender_id)})</strong>
                ${scopeBadge}
                <time class="text-secondary" style="font-size:0.72rem;">${escapeHtml(msg.created_at)}</time>
            </div>
            ${bodyHtml}
        </div>
    `;
    list.appendChild(row);
}

async function uploadSelectedFile() {
    const fileInput = document.getElementById("file-input");
    if (!fileInput.files || fileInput.files.length === 0) return;

    const file = fileInput.files[0];
    const recipientSelect = document.getElementById("recipient-select");
    const targetUserId = recipientSelect.value || "";
    let targetUserName = "";
    if (targetUserId) {
        const opt = recipientSelect.options[recipientSelect.selectedIndex];
        targetUserName = opt ? opt.dataset.userName : targetUserId;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("target_user_id", targetUserId);
    formData.append("target_user_name", targetUserName);

    try {
        const res = await fetch(`/room/${ROOM_ID}/upload`, {
            method: "POST",
            body: formData,
        });
        const data = await res.json();
        if (!data.ok) alert(data.error || "파일 업로드에 실패했습니다.");
    } catch (err) {
        console.error(err);
        alert("파일 업로드 중 오류가 발생했습니다.");
    } finally {
        fileInput.value = "";
    }
}

function appendFileToSidebar(msg) {
    const fileList = document.getElementById("room-file-list");
    if (!fileList) return;
    const emptyItem = document.getElementById("empty-file-item");
    if (emptyItem) emptyItem.remove();

    const kb = ((msg.file_size || 0) / 1024).toFixed(1);
    const li = document.createElement("li");
    li.className = "d-flex align-items-center justify-content-between gap-2 p-2 border rounded-2 small";
    li.innerHTML = `
        <div class="text-truncate">
            <strong class="d-block text-truncate" title="${escapeAttr(msg.file_name)}">${escapeHtml(msg.file_name)}</strong>
            <small class="text-secondary">${escapeHtml(msg.sender_name)} · ${kb} KB</small>
        </div>
        <a href="/download/${msg.file_id}" class="btn btn-outline-secondary btn-sm py-0 px-2 text-nowrap">다운로드</a>
    `;
    fileList.prepend(li);
}

function openNoticeModal() {
    document.getElementById("notice-modal").classList.remove("hidden");
}
function closeNoticeModal() {
    document.getElementById("notice-modal").classList.add("hidden");
}

function submitNewNotice(e) {
    e.preventDefault();
    const input = document.getElementById("new-notice-input");
    const content = input.value.trim();
    if (!content) return;

    socket.emit("set_notice", { room_id: ROOM_ID, content: content });
    input.value = "";
    closeNoticeModal();
}

function deleteActiveNotice() {
    socket.emit("delete_notice", { room_id: ROOM_ID, notice_id: null });
}

function deleteNoticeById(noticeId) {
    socket.emit("delete_notice", { room_id: ROOM_ID, notice_id: noticeId });
    const item = document.getElementById(`notice-item-${noticeId}`);
    if (item) item.remove();
}

socket.on("notice_updated", (data) => {
    const banner = document.getElementById("notice-banner");
    const textEl = document.getElementById("notice-content-text");
    const delBtn = document.getElementById("btn-delete-active-notice");

    if (data.notice) {
        banner.classList.remove("notice-empty");
        delBtn.classList.remove("hidden");
        textEl.innerHTML = `
            ${escapeHtml(data.notice.content)}
            <small class="text-secondary ms-1">(작성: ${escapeHtml(data.notice.author_name)} · ${escapeHtml(data.notice.created_at)})</small>
        `;
    } else {
        banner.classList.add("notice-empty");
        delBtn.classList.add("hidden");
        textEl.textContent = "등록된 공지사항이 없습니다. 우측 버튼을 눌러 공지사항을 설정하세요.";
    }
});

function openScreenShareModal() {
    document.getElementById("screen-share-modal").classList.remove("hidden");
}
function closeScreenShareModal() {
    document.getElementById("screen-share-modal").classList.add("hidden");
}

function openScreenShareForUser(targetUserId) {
    const select = document.getElementById("share-target-select");
    if (select) select.value = targetUserId;
    openScreenShareModal();
}

async function startWindowScreenShare() {
    const targetSelect = document.getElementById("share-target-select");
    const targetUserId = targetSelect ? targetSelect.value : "";
    let targetUserName = "";
    if (targetUserId && targetSelect.selectedIndex >= 0) {
        targetUserName = targetSelect.options[targetSelect.selectedIndex].dataset.userName || targetUserId;
    }

    const customLabel = document.getElementById("share-window-label").value.trim();

    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
        alert("현재 브라우저 환경에서 화면 공유 API(getDisplayMedia)를 호출할 수 없습니다. localhost 또는 HTTPS 환경에서 접속해주세요.");
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getDisplayMedia({
            video: {
                displaySurface: "window",
                cursor: "always",
                width: { ideal: 1280, max: 1600 },
                height: { ideal: 720, max: 900 },
                frameRate: { ideal: 10, max: 15 },
            },
            audio: false,
        });

        closeScreenShareModal();

        const videoTrack = stream.getVideoTracks()[0];
        const settings = videoTrack.getSettings ? videoTrack.getSettings() : {};
        const detectedLabel = customLabel || videoTrack.label || "특정 애플리케이션 Window";

        localScreenStream = stream;
        isSharingScreen = true;

        videoTrack.onended = () => stopScreenShare();

        const panel = document.getElementById("screen-share-panel");
        const localVideo = document.getElementById("local-screen-video");
        const remoteImg = document.getElementById("remote-screen-img");

        panel.classList.remove("hidden");
        remoteImg.classList.add("hidden");
        localVideo.classList.remove("hidden");
        localVideo.srcObject = stream;

        document.getElementById("btn-start-share").classList.add("hidden");
        document.getElementById("btn-stop-share").classList.remove("hidden");
        document.getElementById("btn-panel-stop-share").classList.remove("hidden");

        socket.emit("start_screen_share", {
            room_id: ROOM_ID,
            window_title: detectedLabel,
            display_surface: settings.displaySurface || "window",
            target_user_id: targetUserId || null,
            target_user_name: targetUserName || null,
        });

        const canvas = document.getElementById("screen-capture-canvas");
        const ctx = canvas.getContext("2d");

        if (screenFrameInterval) clearInterval(screenFrameInterval);
        screenFrameInterval = setInterval(() => {
            if (!isSharingScreen || !localVideo.videoWidth) return;

            const maxW = 960;
            const scale = Math.min(1, maxW / localVideo.videoWidth);
            canvas.width = Math.round(localVideo.videoWidth * scale);
            canvas.height = Math.round(localVideo.videoHeight * scale);

            ctx.drawImage(localVideo, 0, 0, canvas.width, canvas.height);
            const dataUrl = canvas.toDataURL("image/jpeg", 0.65);

            socket.emit("screen_frame", { room_id: ROOM_ID, frame: dataUrl });
        }, 200);
    } catch (err) {
        console.warn("화면 공유가 취소되었거나 권한이 거부되었습니다:", err);
    }
}

function stopScreenShare() {
    if (screenFrameInterval) {
        clearInterval(screenFrameInterval);
        screenFrameInterval = null;
    }
    if (localScreenStream) {
        localScreenStream.getTracks().forEach((track) => track.stop());
        localScreenStream = null;
    }
    if (isSharingScreen) {
        isSharingScreen = false;
        socket.emit("stop_screen_share", { room_id: ROOM_ID });
    }
    resetScreenShareUI();
}

function resetScreenShareUI() {
    const panel = document.getElementById("screen-share-panel");
    const localVideo = document.getElementById("local-screen-video");
    const remoteImg = document.getElementById("remote-screen-img");

    panel.classList.add("hidden");
    localVideo.srcObject = null;
    localVideo.classList.add("hidden");
    remoteImg.removeAttribute("src");
    remoteImg.classList.add("hidden");

    document.getElementById("btn-start-share").classList.remove("hidden");
    document.getElementById("btn-stop-share").classList.add("hidden");
    document.getElementById("btn-panel-stop-share").classList.add("hidden");
}

socket.on("screen_share_started", (info) => {
    if (info.room_id !== ROOM_ID) return;

    const panel = document.getElementById("screen-share-panel");
    const statusText = document.getElementById("screen-share-status-text");
    const localVideo = document.getElementById("local-screen-video");
    const remoteImg = document.getElementById("remote-screen-img");

    const scopeText = info.target_user_id
        ? `[1:1 공유 → ${info.target_user_name || info.target_user_id}]`
        : `[전체 공유]`;

    statusText.textContent = `${info.name}(${info.user_id})님의 Window 공유 중 ${scopeText} - 창: ${info.window_title}`;
    panel.classList.remove("hidden");

    if (info.user_id !== CURRENT_USER_ID) {
        localVideo.classList.add("hidden");
        remoteImg.classList.remove("hidden");
    }
});

socket.on("screen_frame", (data) => {
    if (data.room_id !== ROOM_ID || data.user_id === CURRENT_USER_ID) return;
    const panel = document.getElementById("screen-share-panel");
    const remoteImg = document.getElementById("remote-screen-img");
    panel.classList.remove("hidden");
    remoteImg.classList.remove("hidden");
    remoteImg.src = data.frame;
});

socket.on("screen_share_stopped", (data) => {
    if (data.room_id !== ROOM_ID) return;
    if (!isSharingScreen) resetScreenShareUI();
});

function toggleScreenFullscreen() {
    const viewport = document.getElementById("screen-share-viewport");
    if (!document.fullscreenElement) {
        viewport.requestFullscreen().catch(() => {});
    } else {
        document.exitFullscreen().catch(() => {});
    }
}

function openDbHistoryModal() {
    document.getElementById("db-history-modal").classList.remove("hidden");
    loadDbHistory();
}
function closeDbHistoryModal() {
    document.getElementById("db-history-modal").classList.add("hidden");
}

async function loadDbHistory() {
    try {
        const res = await fetch(`/api/room/${ROOM_ID}/history`);
        const data = await res.json();
        if (data.ok) {
            cachedDbData = { notices: data.notices || [], messages: data.messages || [] };
            renderDbHistory(cachedDbData.notices, cachedDbData.messages);
        }
    } catch (err) {
        console.error(err);
    }
}

function filterDbHistory() {
    const q = (document.getElementById("db-search-input").value || "").toLowerCase().trim();
    if (!q) {
        renderDbHistory(cachedDbData.notices, cachedDbData.messages);
        return;
    }
    const filteredNotices = cachedDbData.notices.filter(
        (n) => n.content.toLowerCase().includes(q) || n.author_name.toLowerCase().includes(q) || n.author_id.toLowerCase().includes(q)
    );
    const filteredMessages = cachedDbData.messages.filter(
        (m) => m.content.toLowerCase().includes(q) || m.sender_name.toLowerCase().includes(q) || m.sender_id.toLowerCase().includes(q)
    );
    renderDbHistory(filteredNotices, filteredMessages);
}

function renderDbHistory(notices, messages) {
    const nWrap = document.getElementById("db-notices-container");
    const mWrap = document.getElementById("db-messages-container");

    if (notices.length === 0) {
        nWrap.innerHTML = `<p class="text-secondary small p-2 mb-0">저장된 공지사항이 없습니다.</p>`;
    } else {
        let html = `<table class="table table-sm table-hover mb-0 small"><thead class="table-light sticky-top"><tr><th>ID</th><th>상태</th><th>작성자</th><th>공지 내용</th><th>저장 시각</th></tr></thead><tbody>`;
        notices.forEach((n) => {
            html += `<tr>
                <td>#${n.id}</td>
                <td>${n.is_active ? '<span class="badge text-bg-success">활성</span>' : '<span class="badge text-bg-secondary">이전</span>'}</td>
                <td>${escapeHtml(n.author_name)} (${escapeHtml(n.author_id)})</td>
                <td>${escapeHtml(n.content)}</td>
                <td>${escapeHtml(n.created_at)}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
        nWrap.innerHTML = html;
    }

    if (messages.length === 0) {
        mWrap.innerHTML = `<p class="text-secondary small p-2 mb-0">저장된 메시지가 없습니다.</p>`;
    } else {
        let html = `<table class="table table-sm table-hover mb-0 small"><thead class="table-light sticky-top"><tr><th>ID</th><th>유형/대상</th><th>보낸사람</th><th>내용</th><th>저장 시각</th></tr></thead><tbody>`;
        messages.forEach((m) => {
            const targetLabel = m.target_user_id
                ? `🔒 귓속말 → ${escapeHtml(m.target_user_name || m.target_user_id)}`
                : `전체 (${escapeHtml(m.msg_type)})`;
            html += `<tr>
                <td>#${m.id}</td>
                <td>${targetLabel}</td>
                <td>${escapeHtml(m.sender_name)} (${escapeHtml(m.sender_id)})</td>
                <td>${escapeHtml(m.content)}</td>
                <td>${escapeHtml(m.created_at)}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
        mWrap.innerHTML = html;
    }
}

function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function escapeAttr(str) {
    return escapeHtml(str);
}
