import os
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    send_from_directory,
    abort,
    make_response,
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room, leave_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "openchatting-secret-key-2026")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(DATA_DIR, 'openchat.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload size

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=10 * 1024 * 1024)


# ==========================================
# Database Models
# ==========================================

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class ActiveIPSession(db.Model):
    """동일한 IP에서 1명의 user만 접속할 수 있도록 제어하는 테이블"""
    __tablename__ = "active_ip_sessions"
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.String(64), nullable=False)
    user_name = db.Column(db.String(100), nullable=False)
    session_token = db.Column(db.String(128), nullable=False)
    last_active = db.Column(db.DateTime, default=datetime.now)


class ChatRoom(db.Model):
    __tablename__ = "chat_rooms"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(300), default="")
    creator_id = db.Column(db.String(64), nullable=False)
    creator_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Notice(db.Model):
    """채팅방 공지사항 DB 저장 모델"""
    __tablename__ = "notices"
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = db.Column(db.String(64), nullable=False)
    author_name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "room_id": self.room_id,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "content": self.content,
            "is_active": self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


class UploadedFile(db.Model):
    """업로드된 파일 메타데이터 DB 모델"""
    __tablename__ = "uploaded_files"
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_id = db.Column(db.String(64), nullable=False)
    uploader_name = db.Column(db.String(100), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    file_size = db.Column(db.Integer, nullable=False)
    mime_type = db.Column(db.String(120), default="application/octet-stream")
    created_at = db.Column(db.DateTime, default=datetime.now)


class Message(db.Model):
    """채팅 메시지(전체/귓속말/파일/시스템) DB 저장 모델"""
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = db.Column(db.String(64), nullable=False)
    sender_name = db.Column(db.String(100), nullable=False)
    target_user_id = db.Column(db.String(64), nullable=True, index=True)  # None이면 전체 전송, 값이 있으면 특정 user 전송
    target_user_name = db.Column(db.String(100), nullable=True)
    msg_type = db.Column(db.String(32), default="text")  # text, whisper, file, system
    content = db.Column(db.Text, nullable=False)
    file_id = db.Column(db.Integer, db.ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True)
    file_name = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "room_id": self.room_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "target_user_id": self.target_user_id,
            "target_user_name": self.target_user_name,
            "msg_type": self.msg_type,
            "content": self.content,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


# ==========================================
# In-Memory Real-time Room & Socket Tracking
# ==========================================
# room_participants[room_id] = { sid: {"user_id": ..., "name": ..., "ip": ..., "sharing": False} }
room_participants = {}
# active_screen_sharers[room_id] = {"user_id": ..., "name": ..., "sid": ..., "target_user_id": ..., "window_title": ...}
active_screen_sharers = {}

SESSION_TIMEOUT_MINUTES = 30


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def is_user_connected_via_socket(user_id: str) -> bool:
    for r_id, members in room_participants.items():
        for sid, info in members.items():
            if info.get("user_id") == user_id:
                return True
    return False


def cleanup_stale_ip_sessions():
    """오래되어 비활성화된 IP 세션을 정리합니다."""
    cutoff = datetime.now() - timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    stale_records = ActiveIPSession.query.filter(ActiveIPSession.last_active < cutoff).all()
    changed = False
    for rec in stale_records:
        if not is_user_connected_via_socket(rec.user_id):
            db.session.delete(rec)
            changed = True
    if changed:
        db.session.commit()


def check_and_Touch_ip_session(ip_addr: str, user_id: str, session_token: str) -> tuple[bool, str]:
    """
    동일 IP에서 현재 로그인된 사용자가 유효한지 확인합니다.
    다른 사용자가 해당 IP를 점유 중이면 (False, 사유)를 반환합니다.
    """
    cleanup_stale_ip_sessions()
    ip_rec = ActiveIPSession.query.filter_by(ip_address=ip_addr).first()
    if not ip_rec:
        return False, "IP 세션 정보가 만료되었거나 로그아웃되었습니다."
    if ip_rec.user_id != user_id or ip_rec.session_token != session_token:
        return (
            False,
            f"동일 IP({ip_addr})에서 현재 '{ip_rec.user_name}({ip_rec.user_id})' 계정이 접속 중입니다. (1 IP당 1명만 접속 가능)",
        )
    ip_rec.last_active = datetime.now()
    db.session.commit()
    return True, ""


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        session_token = session.get("session_token")
        if not user_id or not session_token:
            flash("로그인이 필요합니다.", "warning")
            return redirect(url_for("login"))

        ip_addr = get_client_ip()
        ok, reason = check_and_Touch_ip_session(ip_addr, user_id, session_token)
        if not ok:
            session.clear()
            flash(reason, "danger")
            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return decorated_function


# ==========================================
# HTTP Routes - Auth (Register / Login / Logout)
# ==========================================

@app.route("/")
def index():
    if "user_id" in session and "session_token" in session:
        ip_addr = get_client_ip()
        ok, _ = check_and_Touch_ip_session(ip_addr, session["user_id"], session["session_token"])
        if ok:
            return redirect(url_for("lobby"))
        session.clear()
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    ip_addr = get_client_ip()
    cleanup_stale_ip_sessions()
    existing_ip_session = ActiveIPSession.query.filter_by(ip_address=ip_addr).first()

    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        password = request.form.get("password", "")
        remember_id = request.form.get("remember_id") == "on"

        if not user_id or not password:
            flash("User ID와 Password를 모두 입력해주세요.", "danger")
            return render_template(
                "login.html",
                client_ip=ip_addr,
                existing_ip_session=existing_ip_session,
                saved_user_id=user_id,
                remember_id=remember_id,
            )

        user = User.query.filter_by(user_id=user_id).first()
        if not user or not user.check_password(password):
            flash("존재하지 않는 User ID이거나 비밀번호가 일치하지 않습니다.", "danger")
            return render_template(
                "login.html",
                client_ip=ip_addr,
                existing_ip_session=existing_ip_session,
                saved_user_id=user_id,
                remember_id=remember_id,
            )

        # 동일 IP 1인 접속 제한 확인
        existing_ip_session = ActiveIPSession.query.filter_by(ip_address=ip_addr).first()
        if existing_ip_session and existing_ip_session.user_id != user.user_id:
            flash(
                f"접속 제한: 동일한 IP({ip_addr})에서 이미 다른 사용자 "
                f"'{existing_ip_session.user_name}({existing_ip_session.user_id})'님이 접속 중입니다. "
                f"기존 사용자가 로그아웃한 후 접속할 수 있습니다.",
                "danger",
            )
            return render_template(
                "login.html",
                client_ip=ip_addr,
                existing_ip_session=existing_ip_session,
                saved_user_id=user_id,
                remember_id=remember_id,
            )

        token = uuid.uuid4().hex
        if existing_ip_session:
            existing_ip_session.user_id = user.user_id
            existing_ip_session.user_name = user.name
            existing_ip_session.session_token = token
            existing_ip_session.last_active = datetime.now()
        else:
            new_ip_session = ActiveIPSession(
                ip_address=ip_addr,
                user_id=user.user_id,
                user_name=user.name,
                session_token=token,
                last_active=datetime.now(),
            )
            db.session.add(new_ip_session)
        db.session.commit()

        session["user_id"] = user.user_id
        session["user_name"] = user.name
        session["session_token"] = token

        resp = make_response(redirect(url_for("lobby")))
        if remember_id:
            resp.set_cookie("saved_user_id", user.user_id, max_age=60 * 60 * 24 * 30)
        else:
            resp.delete_cookie("saved_user_id")

        flash(f"환영합니다, {user.name}({user.user_id})님!", "success")
        return resp

    saved_user_id = request.cookies.get("saved_user_id", "")
    return render_template(
        "login.html",
        client_ip=ip_addr,
        existing_ip_session=existing_ip_session,
        saved_user_id=saved_user_id,
        remember_id=bool(saved_user_id),
    )


@app.route("/register", methods=["POST"])
def register():
    ip_addr = get_client_ip()
    user_id = request.form.get("user_id", "").strip()
    password = request.form.get("password", "")
    name = request.form.get("name", "").strip()

    if not user_id or not password or not name:
        flash("회원가입 시 User ID, Password, 이름을 모두 입력해야 합니다.", "danger")
        return redirect(url_for("login", tab="register"))

    if len(user_id) < 2 or len(user_id) > 50:
        flash("User ID는 2자 이상 50자 이하로 입력해주세요.", "danger")
        return redirect(url_for("login", tab="register"))

    existing_user = User.query.filter_by(user_id=user_id).first()
    if existing_user:
        flash(f"이미 사용 중인 User ID('{user_id}')입니다. 다른 ID를 선택해주세요.", "danger")
        return redirect(url_for("login", tab="register"))

    new_user = User(user_id=user_id, name=name)
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()

    resp = make_response(redirect(url_for("login")))
    resp.set_cookie("saved_user_id", user_id, max_age=60 * 60 * 24 * 30)
    flash(f"회원가입이 완료되었습니다! '{user_id}' 계정으로 로그인해주세요.", "success")
    return resp


@app.route("/logout", methods=["GET", "POST"])
def logout():
    ip_addr = get_client_ip()
    user_id = session.get("user_id")
    session_token = session.get("session_token")

    if user_id:
        ip_rec = ActiveIPSession.query.filter_by(ip_address=ip_addr).first()
        if ip_rec and (ip_rec.user_id == user_id or ip_rec.session_token == session_token):
            db.session.delete(ip_rec)
            db.session.commit()

    session.clear()
    flash("정상적으로 로그아웃되었습니다.", "info")
    return redirect(url_for("login"))


@app.route("/release-ip", methods=["POST"])
def release_ip():
    """동일 IP에서 이전 브라우저 강제 종료 등으로 세션이 남아있을 때 비밀번호 확인 후 세션을 해제합니다."""
    ip_addr = get_client_ip()
    user_id = request.form.get("user_id", "").strip()
    password = request.form.get("password", "")

    ip_rec = ActiveIPSession.query.filter_by(ip_address=ip_addr).first()
    if not ip_rec:
        flash("현재 해당 IP에 점유된 세션이 없습니다.", "info")
        return redirect(url_for("login"))

    user = User.query.filter_by(user_id=ip_rec.user_id).first()
    if user and user_id == user.user_id and user.check_password(password):
        db.session.delete(ip_rec)
        db.session.commit()
        session.clear()
        flash(f"IP({ip_addr})의 '{user.user_id}' 접속 세션이 해제되었습니다. 이제 로그인할 수 있습니다.", "success")
    else:
        flash("현재 점유 중인 계정의 User ID 또는 비밀번호가 일치하지 않아 해제할 수 없습니다.", "danger")
    return redirect(url_for("login"))


# ==========================================
# HTTP Routes - Lobby & Chat Rooms
# ==========================================

@app.route("/lobby")
@login_required
def lobby():
    rooms = ChatRoom.query.order_by(ChatRoom.created_at.desc()).all()
    room_list = []
    for r in rooms:
        participants = room_participants.get(r.id, {})
        unique_users = {info["user_id"]: info["name"] for info in participants.values()}
        active_notice = (
            Notice.query.filter_by(room_id=r.id, is_active=True)
            .order_by(Notice.created_at.desc())
            .first()
        )
        msg_count = Message.query.filter_by(room_id=r.id).count()
        room_list.append(
            {
                "room": r,
                "participant_count": len(unique_users),
                "participants": list(unique_users.values()),
                "active_notice": active_notice.content if active_notice else None,
                "msg_count": msg_count,
            }
        )
    return render_template(
        "lobby.html",
        room_list=room_list,
        client_ip=get_client_ip(),
    )


@app.route("/rooms/create", methods=["POST"])
@login_required
def create_room():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    initial_notice = request.form.get("initial_notice", "").strip()

    if not title:
        flash("채팅방 이름을 입력해주세요.", "danger")
        return redirect(url_for("lobby"))

    new_room = ChatRoom(
        title=title,
        description=description,
        creator_id=session["user_id"],
        creator_name=session["user_name"],
    )
    db.session.add(new_room)
    db.session.commit()

    if initial_notice:
        notice = Notice(
            room_id=new_room.id,
            author_id=session["user_id"],
            author_name=session["user_name"],
            content=initial_notice,
            is_active=True,
        )
        db.session.add(notice)
        db.session.commit()

    flash(f"'{title}' 오픈채팅방이 개설되었습니다!", "success")
    return redirect(url_for("enter_room", room_id=new_room.id))


@app.route("/room/<int:room_id>")
@login_required
def enter_room(room_id: int):
    room = ChatRoom.query.get_or_404(room_id)
    current_user_id = session["user_id"]

    # DB에 저장된 공지사항 불러오기
    active_notice = (
        Notice.query.filter_by(room_id=room_id, is_active=True)
        .order_by(Notice.created_at.desc())
        .first()
    )
    all_notices = (
        Notice.query.filter_by(room_id=room_id)
        .order_by(Notice.created_at.desc())
        .limit(50)
        .all()
    )

    # DB에 저장된 메시지 불러오기 (전체 메시지 + 본인이 송/수신한 귓속말 메시지)
    messages = (
        Message.query.filter(
            Message.room_id == room_id,
            db.or_(
                Message.target_user_id.is_(None),
                Message.target_user_id == "",
                Message.sender_id == current_user_id,
                Message.target_user_id == current_user_id,
            ),
        )
        .order_by(Message.created_at.asc())
        .limit(300)
        .all()
    )

    files = (
        UploadedFile.query.filter_by(room_id=room_id)
        .order_by(UploadedFile.created_at.desc())
        .limit(50)
        .all()
    )

    return render_template(
        "room.html",
        room=room,
        active_notice=active_notice,
        all_notices=all_notices,
        messages=messages,
        files=files,
        client_ip=get_client_ip(),
    )


# ==========================================
# HTTP Routes - File Upload / Download & DB History API
# ==========================================

@app.route("/room/<int:room_id>/upload", methods=["POST"])
@login_required
def upload_file(room_id: int):
    room = ChatRoom.query.get_or_404(room_id)
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "업로드할 파일이 선택되지 않았습니다."}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "유효한 파일 이름이 없습니다."}), 400

    target_user_id = request.form.get("target_user_id", "").strip() or None
    target_user_name = request.form.get("target_user_name", "").strip() or None

    original_filename = f.filename
    safe_ext = os.path.splitext(secure_filename(original_filename))[1]
    stored_filename = f"{uuid.uuid4().hex}{safe_ext}"
    save_path = os.path.join(UPLOAD_DIR, stored_filename)
    f.save(save_path)
    file_size = os.path.getsize(save_path)

    uploaded = UploadedFile(
        room_id=room.id,
        uploader_id=session["user_id"],
        uploader_name=session["user_name"],
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_size=file_size,
        mime_type=f.mimetype or "application/octet-stream",
    )
    db.session.add(uploaded)
    db.session.commit()

    msg = Message(
        room_id=room.id,
        sender_id=session["user_id"],
        sender_name=session["user_name"],
        target_user_id=target_user_id,
        target_user_name=target_user_name,
        msg_type="file",
        content=f"파일 공유: {original_filename}",
        file_id=uploaded.id,
        file_name=original_filename,
        file_size=file_size,
    )
    db.session.add(msg)
    db.session.commit()

    payload = msg.to_dict()
    payload["mime_type"] = uploaded.mime_type
    payload["download_url"] = url_for("download_file", file_id=uploaded.id)

    # 실시간 전송 (전체 또는 특정 User)
    if target_user_id:
        for sid, info in room_participants.get(room.id, {}).items():
            if info["user_id"] in (session["user_id"], target_user_id):
                socketio.emit("new_message", payload, to=sid)
    else:
        socketio.emit("new_message", payload, to=f"room_{room.id}")

    return jsonify({"ok": True, "message": payload})


@app.route("/download/<int:file_id>")
@login_required
def download_file(file_id: int):
    uploaded = UploadedFile.query.get_or_404(file_id)
    file_path = os.path.join(UPLOAD_DIR, uploaded.stored_filename)
    if not os.path.exists(file_path):
        abort(404, description="서버에서 파일을 찾을 수 없습니다.")
    return send_from_directory(
        UPLOAD_DIR,
        uploaded.stored_filename,
        as_attachment=True,
        download_name=uploaded.original_filename,
    )


@app.route("/preview/<int:file_id>")
@login_required
def preview_file(file_id: int):
    uploaded = UploadedFile.query.get_or_404(file_id)
    file_path = os.path.join(UPLOAD_DIR, uploaded.stored_filename)
    if not os.path.exists(file_path):
        abort(404)
    return send_from_directory(
        UPLOAD_DIR,
        uploaded.stored_filename,
        as_attachment=False,
        mimetype=uploaded.mime_type,
    )


@app.route("/api/room/<int:room_id>/history")
@login_required
def api_room_history(room_id: int):
    """DB에 저장된 공지사항 및 메시지 내역을 조회하는 API"""
    current_user_id = session["user_id"]
    notices = (
        Notice.query.filter_by(room_id=room_id)
        .order_by(Notice.created_at.desc())
        .all()
    )
    messages = (
        Message.query.filter(
            Message.room_id == room_id,
            db.or_(
                Message.target_user_id.is_(None),
                Message.target_user_id == "",
                Message.sender_id == current_user_id,
                Message.target_user_id == current_user_id,
            ),
        )
        .order_by(Message.created_at.desc())
        .limit(200)
        .all()
    )
    return jsonify(
        {
            "ok": True,
            "notices": [n.to_dict() for n in notices],
            "messages": [m.to_dict() for m in messages],
        }
    )


# ==========================================
# Socket.IO Events - Chat, Whisper, Notice, User List, Screen Share
# ==========================================

def broadcast_user_list(room_id: int):
    """채팅방 접속자 리스트를 중복 없이 집계하여 방 전체에 브로드캐스트합니다."""
    members = room_participants.get(room_id, {})
    sharer = active_screen_sharers.get(room_id)
    sharer_user_id = sharer["user_id"] if sharer else None

    users_by_id = {}
    for sid, info in members.items():
        uid = info["user_id"]
        users_by_id[uid] = {
            "user_id": uid,
            "name": info["name"],
            "ip": info["ip"],
            "sharing": uid == sharer_user_id,
        }

    user_list = list(users_by_id.values())
    socketio.emit(
        "user_list_update",
        {
            "room_id": room_id,
            "count": len(user_list),
            "users": user_list,
            "active_sharer": sharer,
        },
        to=f"room_{room_id}",
    )


@socketio.on("join_chat_room")
def handle_join_chat_room(data):
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    session_token = session.get("session_token")
    if not user_id or not session_token:
        emit("auth_error", {"message": "로그인이 필요합니다."})
        return

    ip_addr = get_client_ip()
    ok, reason = check_and_Touch_ip_session(ip_addr, user_id, session_token)
    if not ok:
        emit("auth_error", {"message": reason})
        return

    room_id = int(data.get("room_id", 0))
    room_key = f"room_{room_id}"
    join_room(room_key)

    if room_id not in room_participants:
        room_participants[room_id] = {}

    already_in_room = any(
        info["user_id"] == user_id for info in room_participants[room_id].values()
    )

    room_participants[room_id][request.sid] = {
        "user_id": user_id,
        "name": user_name,
        "ip": ip_addr,
    }

    if not already_in_room:
        sys_msg = Message(
            room_id=room_id,
            sender_id="SYSTEM",
            sender_name="시스템",
            msg_type="system",
            content=f"{user_name}({user_id})님이 채팅방에 입장하셨습니다.",
        )
        db.session.add(sys_msg)
        db.session.commit()
        socketio.emit("new_message", sys_msg.to_dict(), to=room_key)

    broadcast_user_list(room_id)

    # 현재 진행 중인 화면 공유가 있다면 새로 입장한 사용자에게 알림
    sharer = active_screen_sharers.get(room_id)
    if sharer:
        target_uid = sharer.get("target_user_id")
        if not target_uid or target_uid == user_id or sharer["user_id"] == user_id:
            emit("screen_share_started", sharer)


@socketio.on("send_message")
def handle_send_message(data):
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    content = (data.get("content") or "").strip()
    target_user_id = (data.get("target_user_id") or "").strip() or None
    target_user_name = (data.get("target_user_name") or "").strip() or None

    if not content:
        return

    # 수신자 이름 보정
    if target_user_id and not target_user_name:
        target_user = User.query.filter_by(user_id=target_user_id).first()
        if target_user:
            target_user_name = target_user.name
        else:
            target_user_name = target_user_id

    msg_type = "whisper" if target_user_id else "text"

    msg = Message(
        room_id=room_id,
        sender_id=user_id,
        sender_name=user_name,
        target_user_id=target_user_id,
        target_user_name=target_user_name,
        msg_type=msg_type,
        content=content,
    )
    db.session.add(msg)
    db.session.commit()

    payload = msg.to_dict()

    if target_user_id:
        # 특정 user에게만 전송 (본인과 대상 user의 소켓에만 전달)
        for sid, info in room_participants.get(room_id, {}).items():
            if info["user_id"] in (user_id, target_user_id):
                socketio.emit("new_message", payload, to=sid)
    else:
        # 전체 전송
        socketio.emit("new_message", payload, to=f"room_{room_id}")


@socketio.on("set_notice")
def handle_set_notice(data):
    """채팅방 공지사항 설정 및 DB 저장"""
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    content = (data.get("content") or "").strip()
    if not content:
        return

    # 기존 활성 공지사항 비활성화 처리 (이력은 DB에 유지)
    Notice.query.filter_by(room_id=room_id, is_active=True).update({"is_active": False})

    new_notice = Notice(
        room_id=room_id,
        author_id=user_id,
        author_name=user_name,
        content=content,
        is_active=True,
    )
    db.session.add(new_notice)

    sys_msg = Message(
        room_id=room_id,
        sender_id="SYSTEM",
        sender_name="공지 알림",
        msg_type="system",
        content=f"📢 {user_name}({user_id})님이 새 공지사항을 등록했습니다: {content}",
    )
    db.session.add(sys_msg)
    db.session.commit()

    room_key = f"room_{room_id}"
    socketio.emit(
        "notice_updated",
        {"action": "set", "notice": new_notice.to_dict()},
        to=room_key,
    )
    socketio.emit("new_message", sys_msg.to_dict(), to=room_key)


@socketio.on("delete_notice")
def handle_delete_notice(data):
    """채팅방 공지사항 삭제 처리"""
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    notice_id = data.get("notice_id")

    if notice_id:
        notice = Notice.query.filter_by(id=int(notice_id), room_id=room_id).first()
        if notice:
            db.session.delete(notice)
    else:
        # 현재 활성화된 공지 삭제
        active_notices = Notice.query.filter_by(room_id=room_id, is_active=True).all()
        for n in active_notices:
            db.session.delete(n)

    sys_msg = Message(
        room_id=room_id,
        sender_id="SYSTEM",
        sender_name="공지 알림",
        msg_type="system",
        content=f"🗑️ {user_name}({user_id})님이 공지사항을 삭제했습니다.",
    )
    db.session.add(sys_msg)
    db.session.commit()

    # 남아있는 가장 최신 활성 공지 조회
    latest_active = (
        Notice.query.filter_by(room_id=room_id, is_active=True)
        .order_by(Notice.created_at.desc())
        .first()
    )

    room_key = f"room_{room_id}"
    socketio.emit(
        "notice_updated",
        {
            "action": "delete",
            "deleted_id": notice_id,
            "notice": latest_active.to_dict() if latest_active else None,
        },
        to=room_key,
    )
    socketio.emit("new_message", sys_msg.to_dict(), to=room_key)


@socketio.on("start_screen_share")
def handle_start_screen_share(data):
    """특정 Window 화면 공유 시작 이벤트"""
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    window_title = (data.get("window_title") or "선택된 창(Window)").strip()
    display_surface = (data.get("display_surface") or "window").strip()
    target_user_id = (data.get("target_user_id") or "").strip() or None
    target_user_name = (data.get("target_user_name") or "").strip() or None

    sharer_info = {
        "room_id": room_id,
        "user_id": user_id,
        "name": user_name,
        "sid": request.sid,
        "window_title": window_title,
        "display_surface": display_surface,
        "target_user_id": target_user_id,
        "target_user_name": target_user_name,
        "started_at": datetime.now().strftime("%H:%M:%S"),
    }
    active_screen_sharers[room_id] = sharer_info

    scope_desc = f"{target_user_name}({target_user_id})님에게만" if target_user_id else "방 전체에"
    sys_msg = Message(
        room_id=room_id,
        sender_id="SYSTEM",
        sender_name="화면 공유",
        target_user_id=target_user_id,
        target_user_name=target_user_name,
        msg_type="system",
        content=f"🖥️ {user_name}({user_id})님이 윈도우 화면 공유를 시작했습니다 ({scope_desc} | 공유 창: {window_title})",
    )
    db.session.add(sys_msg)
    db.session.commit()

    if target_user_id:
        for sid, info in room_participants.get(room_id, {}).items():
            if info["user_id"] in (user_id, target_user_id):
                socketio.emit("screen_share_started", sharer_info, to=sid)
                socketio.emit("new_message", sys_msg.to_dict(), to=sid)
    else:
        room_key = f"room_{room_id}"
        socketio.emit("screen_share_started", sharer_info, to=room_key)
        socketio.emit("new_message", sys_msg.to_dict(), to=room_key)

    broadcast_user_list(room_id)


@socketio.on("screen_frame")
def handle_screen_frame(data):
    """공유 중인 특정 Window의 실시간 영상 프레임 전송"""
    user_id = session.get("user_id")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    sharer = active_screen_sharers.get(room_id)
    if not sharer or sharer["user_id"] != user_id:
        return

    frame_data = data.get("frame")
    if not frame_data:
        return

    target_user_id = sharer.get("target_user_id")
    payload = {
        "room_id": room_id,
        "user_id": user_id,
        "name": sharer["name"],
        "window_title": sharer["window_title"],
        "frame": frame_data,
    }

    if target_user_id:
        for sid, info in room_participants.get(room_id, {}).items():
            if info["user_id"] == target_user_id and sid != request.sid:
                socketio.emit("screen_frame", payload, to=sid)
    else:
        socketio.emit(
            "screen_frame",
            payload,
            to=f"room_{room_id}",
             include_self=False,
        )


@socketio.on("stop_screen_share")
def handle_stop_screen_share(data):
    """화면 공유 취소/중지 이벤트"""
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    sharer = active_screen_sharers.get(room_id)
    if sharer and sharer["user_id"] == user_id:
        target_user_id = sharer.get("target_user_id")
        target_user_name = sharer.get("target_user_name")
        del active_screen_sharers[room_id]

        sys_msg = Message(
            room_id=room_id,
            sender_id="SYSTEM",
            sender_name="화면 공유",
            target_user_id=target_user_id,
            target_user_name=target_user_name,
            msg_type="system",
            content=f"⏹️ {user_name}({user_id})님이 화면 공유를 취소(종료)했습니다.",
        )
        db.session.add(sys_msg)
        db.session.commit()

        room_key = f"room_{room_id}"
        socketio.emit(
            "screen_share_stopped",
            {"room_id": room_id, "user_id": user_id, "name": user_name},
            to=room_key,
        )
        if target_user_id:
            for sid, info in room_participants.get(room_id, {}).items():
                if info["user_id"] in (user_id, target_user_id):
                    socketio.emit("new_message", sys_msg.to_dict(), to=sid)
        else:
            socketio.emit("new_message", sys_msg.to_dict(), to=room_key)

        broadcast_user_list(room_id)


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    for room_id, members in list(room_participants.items()):
        if sid in members:
            leaving_info = members.pop(sid)
            uid = leaving_info["user_id"]
            uname = leaving_info["name"]
            leave_room(f"room_{room_id}")

            # 해당 방에서 공유 중이던 소켓이 끊어졌으면 화면 공유 종료
            sharer = active_screen_sharers.get(room_id)
            if sharer and sharer.get("sid") == sid:
                del active_screen_sharers[room_id]
                socketio.emit(
                    "screen_share_stopped",
                    {"room_id": room_id, "user_id": uid, "name": uname},
                    to=f"room_{room_id}",
                )

            still_in_room = any(info["user_id"] == uid for info in members.values())
            if not still_in_room:
                sys_msg = Message(
                    room_id=room_id,
                    sender_id="SYSTEM",
                    sender_name="시스템",
                    msg_type="system",
                    content=f"{uname}({uid})님이 채팅방에서 퇴장하셨습니다.",
                )
                db.session.add(sys_msg)
                db.session.commit()
                socketio.emit("new_message", sys_msg.to_dict(), to=f"room_{room_id}")

            broadcast_user_list(room_id)


def init_db():
    with app.app_context():
        db.create_all()
        if ChatRoom.query.count() == 0:
            default_room = ChatRoom(
                title="오픈 메인 채팅방",
                description="누구나 자유롭게 대화하고 파일 및 윈도우 화면을 공유할 수 있는 기본 오픈채팅방입니다.",
                creator_id="admin",
                creator_name="관리자",
            )
            db.session.add(default_room)
            db.session.commit()

            welcome_notice = Notice(
                room_id=default_room.id,
                author_id="admin",
                author_name="관리자",
                content="오픈채팅방에 오신 것을 환영합니다! 전체/특정 사용자 메시지 전송, 공지사항 설정, 파일 업로드/다운로드, 특정 Window 화면 공유 기능을 사용할 수 있습니다.",
                is_active=True,
            )
            db.session.add(welcome_notice)
            db.session.commit()


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4000))
    print(f"Starting OpenChatting Flask Server on http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
