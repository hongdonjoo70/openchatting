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
BOARD_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "board")
os.makedirs(BOARD_UPLOAD_DIR, exist_ok=True)

MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50MB max upload size
MAX_TOTAL_STORAGE_BYTES = 1024 * 1024 * 1024  # 1GB max total upload storage across all files


def format_file_size_limit(size_bytes: int) -> str:
    gb = size_bytes / (1024 * 1024 * 1024)
    if gb >= 1:
        return f"{int(gb)}GB" if gb.is_integer() else f"{gb:.2f}GB"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1:
        return f"{int(mb)}MB" if mb.is_integer() else f"{mb:.1f}MB"
    kb = size_bytes / 1024
    if kb >= 1:
        return f"{int(kb)}KB" if kb.is_integer() else f"{kb:.1f}KB"
    return f"{size_bytes}B"


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "openchatting-secret-key-2026")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(DATA_DIR, 'openchat.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE_BYTES

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=10 * 1024 * 1024)


@app.errorhandler(413)
def handle_request_entity_too_large(error):
    max_bytes = app.config.get("MAX_CONTENT_LENGTH", MAX_UPLOAD_SIZE_BYTES)
    max_label = format_file_size_limit(max_bytes)
    return (
        jsonify(
            {
                "ok": False,
                "error": f"File 크기가 너무 큽니다. 최대 upload가능한 File 크기는 {max_label} 입니다.",
            }
        ),
        413,
    )


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
class BoardPost(db.Model):
    """로그인 화면 게시판 모델"""
    __tablename__ = "board_posts"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.String(64), nullable=False, index=True)
    author_name = db.Column(db.String(100), nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    original_image_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "has_image": bool(self.image_filename),
            "image_url": url_for("board_image", filename=self.image_filename) if self.image_filename else None,
            "original_image_name": self.original_image_name,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


class ChatPoll(db.Model):
    """채팅방 투표 모델"""
    __tablename__ = "chat_polls"
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    creator_id = db.Column(db.String(64), nullable=False)
    creator_name = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    is_multiple = db.Column(db.Boolean, default=False)  # False: 단수 선택, True: 복수 선택
    deadline = db.Column(db.DateTime, nullable=False)  # 투표 마감 시간
    is_closed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    options = db.relationship("ChatPollOption", backref="poll", cascade="all, delete-orphan", lazy="joined", order_by="ChatPollOption.id")
    votes = db.relationship("ChatPollVote", backref="poll", cascade="all, delete-orphan", lazy="select")

    def check_is_closed(self) -> bool:
        if self.is_closed:
            return True
        if self.deadline and datetime.now() >= self.deadline:
            self.is_closed = True
            counts = {opt.id: 0 for opt in self.options}
            for v in self.votes:
                if v.option_id in counts:
                    counts[v.option_id] += 1
            max_v = max(counts.values()) if counts else 0
            winners = [f"{opt.option_text}({counts[opt.id]}표)" for opt in self.options if max_v > 0 and counts[opt.id] == max_v]
            winner_str = f"최다 득표: {', '.join(winners)}" if winners else "투표자 없음"
            sys_msg = Message(
                room_id=self.room_id,
                sender_id="SYSTEM",
                sender_name="투표 마감 알림",
                msg_type="system",
                content=f"🏁 '{self.title}' 투표가 마감되어 최종 결과가 업데이트되었습니다 ({winner_str}).",
            )
            db.session.add(sys_msg)
            db.session.commit()
            try:
                socketio.emit("new_message", sys_msg.to_dict(), to=f"room_{self.room_id}")
            except Exception:
                pass
            return True
        return False

    def to_dict(self, current_user_id=None):
        closed = self.check_is_closed()
        total_votes_count = len(self.votes)
        unique_voters = {v.user_id for v in self.votes}

        my_voted_options = []
        if current_user_id:
            my_voted_options = [v.option_id for v in self.votes if v.user_id == current_user_id]

        max_opt_votes = 0
        options_data = []
        for opt in self.options:
            opt_votes = [v for v in self.votes if v.option_id == opt.id]
            vote_count = len(opt_votes)
            if vote_count > max_opt_votes:
                max_opt_votes = vote_count
            voters_list = [v.user_name for v in opt_votes]
            options_data.append({
                "id": opt.id,
                "option_text": opt.option_text,
                "vote_count": vote_count,
                "voters": voters_list,
                "voted_by_me": opt.id in my_voted_options,
                "percentage": round((vote_count / total_votes_count * 100), 1) if total_votes_count > 0 else 0,
            })

        for opt_dict in options_data:
            opt_dict["is_winner"] = (closed and max_opt_votes > 0 and opt_dict["vote_count"] == max_opt_votes)

        return {
            "id": self.id,
            "room_id": self.room_id,
            "creator_id": self.creator_id,
            "creator_name": self.creator_name,
            "title": self.title,
            "is_multiple": self.is_multiple,
            "choice_type_label": "복수 선택" if self.is_multiple else "단수 선택",
            "deadline": self.deadline.strftime("%Y-%m-%d %H:%M:%S"),
            "deadline_iso": self.deadline.isoformat(),
            "is_closed": closed,
            "status_label": "마감됨" if closed else "진행 중",
            "total_votes": total_votes_count,
            "voter_count": len(unique_voters),
            "my_voted_option_ids": my_voted_options,
            "has_voted": len(my_voted_options) > 0,
            "options": options_data,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


class ChatPollOption(db.Model):
    """투표 선택 항목 모델"""
    __tablename__ = "chat_poll_options"
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("chat_polls.id", ondelete="CASCADE"), nullable=False, index=True)
    option_text = db.Column(db.String(200), nullable=False)


class ChatPollVote(db.Model):
    """투표 참여 내역 모델"""
    __tablename__ = "chat_poll_votes"
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("chat_polls.id", ondelete="CASCADE"), nullable=False, index=True)
    option_id = db.Column(db.Integer, db.ForeignKey("chat_poll_options.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.String(64), nullable=False, index=True)
    user_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


SERVER_BOOT_ID = uuid.uuid4().hex
# room_participants[room_id] = { sid: {"user_id": ..., "name": ..., "ip": ..., "sharing": False} }
room_participants = {}
# lobby_participants[sid] = {"user_id": ..., "name": ..., "ip": ...}
lobby_participants = {}
# active_screen_sharers[room_id] = {"user_id": ..., "name": ..., "sid": ..., "target_user_id": ..., "window_title": ...}
active_screen_sharers = {}

SESSION_TIMEOUT_MINUTES = 30
DISCONNECT_GRACE_SECONDS = 3
RETENTION_HOURS = 48
MAX_ROOMS = 6
MAX_ROOM_PARTICIPANTS = 6


def get_total_uploaded_size() -> int:
    """전체 업로드된 파일의 총 용량(bytes)을 반환합니다."""
    total = db.session.query(db.func.sum(UploadedFile.file_size)).scalar()
    return int(total or 0)


def perform_delete_file(file_id: int, user_id: str, user_name: str, room_id: int = None):
    """특정 업로드 파일을 디스크 및 DB에서 삭제하고 연관 메시지를 갱신합니다."""
    uploaded = UploadedFile.query.get(file_id)
    if not uploaded:
        return False, "파일을 찾을 수 없습니다."

    target_room_id = uploaded.room_id or room_id
    filename = uploaded.original_filename

    # 1. 디스크에서 실제 파일 삭제
    file_path = os.path.join(UPLOAD_DIR, uploaded.stored_filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass

    # 2. 메시지 기록 업데이트 (연결된 메시지의 file_id를 null로 하고 삭제된 파일 표시)
    msgs = Message.query.filter_by(file_id=file_id).all()
    for m in msgs:
        m.file_id = None
        m.content = f"(삭제된 파일: {filename})"

    # 3. DB UploadedFile 레코드 삭제
    db.session.delete(uploaded)

    # 4. 파일 삭제 시스템 알림 메시지 생성
    sys_msg = None
    if target_room_id:
        sys_msg = Message(
            room_id=target_room_id,
            sender_id="SYSTEM",
            sender_name="파일 알림",
            msg_type="system",
            content=f"🗑️ {user_name}({user_id})님이 파일 '{filename}'을 삭제했습니다.",
        )
        db.session.add(sys_msg)

    db.session.commit()

    new_total = get_total_uploaded_size()
    new_total_label = format_file_size_limit(new_total)

    # 실시간 소켓 브로드캐스트
    if target_room_id:
        room_key = f"room_{target_room_id}"
        socketio.emit(
            "file_deleted",
            {
                "file_id": file_id,
                "room_id": target_room_id,
                "file_name": filename,
                "deleted_by": user_name,
                "total_bytes": new_total,
                "total_label": new_total_label,
            },
            to=room_key,
        )
        if sys_msg:
            socketio.emit("new_message", sys_msg.to_dict(), to=room_key)

    socketio.emit(
        "storage_updated",
        {
            "total_bytes": new_total,
            "total_label": new_total_label,
            "max_label": "1GB",
        },
    )
    return True, None


def cleanup_expired_data():
    """공지사항(Notice)을 제외하고 48시간이 지난 업로드 파일(실제 파일 및 DB)과 채팅 메시지(Message)를 모두 삭제합니다."""
    cutoff = datetime.now() - timedelta(hours=RETENTION_HOURS)
    changed = False

    expired_files = UploadedFile.query.filter(UploadedFile.created_at < cutoff).all()
    for f in expired_files:
        file_path = os.path.join(UPLOAD_DIR, f.stored_filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        msgs = Message.query.filter_by(file_id=f.id).all()
        for m in msgs:
            m.file_id = None
            m.content = f"(삭제된 파일: {f.original_filename})"
        db.session.delete(f)
        changed = True

    deleted_msgs = Message.query.filter(Message.created_at < cutoff).delete(synchronize_session=False)
    if deleted_msgs > 0:
        changed = True

    if changed:
        db.session.commit()


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def is_user_connected_via_socket(user_id: str) -> bool:
    for info in lobby_participants.values():
        if info.get("user_id") == user_id:
            return True
    for r_id, members in room_participants.items():
        for sid, info in members.items():
            if info.get("user_id") == user_id:
                return True
    return False


def logout_user_ip_session(user_id: str = None, ip_addr: str = None, session_token: str = None):
    """특정 사용자 또는 IP의 활성 세션을 DB에서 제거(Logout)합니다."""
    query = ActiveIPSession.query
    if ip_addr:
        query = query.filter_by(ip_address=ip_addr)
    if user_id:
        query = query.filter_by(user_id=user_id)
    if session_token:
        query = query.filter_by(session_token=session_token)
    records = query.all()
    if records:
        for rec in records:
            db.session.delete(rec)
        db.session.commit()


def cleanup_stale_ip_sessions(immediate_if_disconnected: bool = False):
    """접속 해제되었거나 비활성화된 IP 세션을 즉시 또는 유예 시간 후 정리(Logout)합니다."""
    all_records = ActiveIPSession.query.all()
    now = datetime.now()
    changed = False
    for rec in all_records:
        if is_user_connected_via_socket(rec.user_id):
            continue
        elapsed = (now - rec.last_active).total_seconds()
        if immediate_if_disconnected or elapsed > DISCONNECT_GRACE_SECONDS:
            db.session.delete(rec)
            changed = True
    if changed:
        db.session.commit()


def check_and_Touch_ip_session(ip_addr: str, user_id: str, session_token: str) -> tuple[bool, str]:
    """
    동일 IP에서 현재 로그인된 사용자가 유효한지 확인합니다.
    다른 사용자가 해당 IP를 점유 중이면 (False, 사유)를 반환합니다.
    """
    if session.get("boot_id") != SERVER_BOOT_ID:
        return False, "시스템이 재시작되어 세션이 초기화되었습니다. 다시 로그인해주세요."

    cleanup_stale_ip_sessions()
    ip_rec = ActiveIPSession.query.filter_by(ip_address=ip_addr).first()
    if not ip_rec:
        return False, "접속이 해제되어 로그아웃되었습니다. 다시 로그인해주세요."
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
        if not user_id or not session_token or session.get("boot_id") != SERVER_BOOT_ID:
            session.clear()
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
    """시스템 초기 접속 시 항상 메인(로그인) 화면부터 시작하도록 처리"""
    ip_addr = get_client_ip()
    user_id = session.get("user_id")
    session_token = session.get("session_token")
    if user_id and not is_user_connected_via_socket(user_id):
        logout_user_ip_session(user_id=user_id, ip_addr=ip_addr, session_token=session_token)
    session.clear()
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    ip_addr = get_client_ip()
    # 소켓 연결이 끊어진(접속 해제된) 기존 IP 세션은 즉시 로그아웃 처리
    cleanup_stale_ip_sessions(immediate_if_disconnected=True)
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
        session["boot_id"] = SERVER_BOOT_ID

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

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def is_allowed_image(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS


@app.route("/board/image/<path:filename>")
def board_image(filename):
    return send_from_directory(BOARD_UPLOAD_DIR, filename)


@app.route("/api/board/<int:post_id>")
@login_required
def api_board_get(post_id: int):
    post = BoardPost.query.get_or_404(post_id)
    data = post.to_dict()
    data["is_author"] = (session.get("user_id") == post.author_id)
    return jsonify({"ok": True, "post": data})


@app.route("/board/create", methods=["POST"])
@login_required
def board_create():
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    if not title or not content:
        flash("게시글 제목과 내용을 모두 입력해주세요.", "danger")
        return redirect(url_for("lobby"))

    stored_image_name = None
    orig_image_name = None
    if "image" in request.files:
        img_file = request.files["image"]
        if img_file and img_file.filename:
            orig_name = img_file.filename
            if not is_allowed_image(orig_name):
                flash("그림은 이미지 파일(.jpg, .jpeg, .png, .gif, .webp)만 업로드할 수 있습니다.", "danger")
                return redirect(url_for("lobby"))
            ext = os.path.splitext(secure_filename(orig_name))[1] or os.path.splitext(orig_name)[1].lower()
            stored_image_name = f"board_{uuid.uuid4().hex}{ext}"
            img_file.save(os.path.join(BOARD_UPLOAD_DIR, stored_image_name))
            orig_image_name = orig_name

    post = BoardPost(
        title=title,
        content=content,
        author_id=session["user_id"],
        author_name=session.get("user_name", session["user_id"]),
        image_filename=stored_image_name,
        original_image_name=orig_image_name,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.session.add(post)
    db.session.commit()

    flash(f"게시글 '{title}'이(가) 등록되었습니다.", "success")
    return redirect(url_for("lobby"))


@app.route("/board/<int:post_id>/update", methods=["POST"])
@login_required
def board_update(post_id: int):
    post = BoardPost.query.get_or_404(post_id)

    # 등록한 사람만 수정 가능
    if session.get("user_id") != post.author_id:
        flash("게시글을 수정할 권한이 없습니다. (등록한 사람만 수정 가능)", "danger")
        return redirect(url_for("lobby"))

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    if not title or not content:
        flash("게시글 제목과 내용을 모두 입력해주세요.", "danger")
        return redirect(url_for("lobby"))

    post.title = title
    post.content = content

    # 기존 그림 삭제 여부
    if request.form.get("remove_image") == "on" and post.image_filename:
        old_path = os.path.join(BOARD_UPLOAD_DIR, post.image_filename)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
        post.image_filename = None
        post.original_image_name = None

    # 새 그림 업로드 시 교체
    if "image" in request.files:
        img_file = request.files["image"]
        if img_file and img_file.filename:
            orig_name = img_file.filename
            if not is_allowed_image(orig_name):
                flash("그림은 이미지 파일(.jpg, .jpeg, .png, .gif, .webp)만 업로드할 수 있습니다.", "danger")
                return redirect(url_for("lobby"))
            if post.image_filename:
                old_path = os.path.join(BOARD_UPLOAD_DIR, post.image_filename)
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
            ext = os.path.splitext(secure_filename(orig_name))[1] or os.path.splitext(orig_name)[1].lower()
            stored_image_name = f"board_{uuid.uuid4().hex}{ext}"
            img_file.save(os.path.join(BOARD_UPLOAD_DIR, stored_image_name))
            post.image_filename = stored_image_name
            post.original_image_name = orig_name

    post.updated_at = datetime.now()
    db.session.commit()

    flash(f"게시글 '{title}'이(가) 수정되었습니다.", "success")
    return redirect(url_for("lobby"))


@app.route("/board/<int:post_id>/delete", methods=["POST"])
@login_required
def board_delete(post_id: int):
    post = BoardPost.query.get_or_404(post_id)

    # 등록한 사람만 삭제 가능
    if session.get("user_id") != post.author_id:
        flash("게시글을 삭제할 권한이 없습니다. (등록한 사람만 삭제 가능)", "danger")
        return redirect(url_for("lobby"))

    if post.image_filename:
        old_path = os.path.join(BOARD_UPLOAD_DIR, post.image_filename)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    post_title = post.title
    db.session.delete(post)
    db.session.commit()

    flash(f"게시글 '{post_title}'이(가) 삭제되었습니다.", "success")
    return redirect(url_for("lobby"))


@app.route("/lobby")
@login_required
def lobby():
    cleanup_expired_data()
    rooms = ChatRoom.query.order_by(ChatRoom.created_at.desc()).limit(MAX_ROOMS).all()
    room_list = []
    for r in rooms:
        participants = room_participants.get(r.id, {})
        unique_users = {info["user_id"]: info["name"] for info in participants.values()}
        active_notice = (
            Notice.query.filter_by(room_id=r.id)
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
                "is_full": len(unique_users) >= MAX_ROOM_PARTICIPANTS,
            }
        )
    total_storage = get_total_uploaded_size()
    board_posts = BoardPost.query.order_by(BoardPost.updated_at.desc(), BoardPost.id.desc()).all()
    return render_template(
        "lobby.html",
        room_list=room_list,
        max_rooms=MAX_ROOMS,
        max_participants=MAX_ROOM_PARTICIPANTS,
        client_ip=get_client_ip(),
        total_storage_bytes=total_storage,
        total_storage_label=format_file_size_limit(total_storage),
        max_storage_label=format_file_size_limit(MAX_TOTAL_STORAGE_BYTES),
        board_posts=board_posts,
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

    if ChatRoom.query.count() >= MAX_ROOMS:
        flash(f"채팅방은 최대 {MAX_ROOMS}개까지만 개설할 수 있습니다.", "danger")
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
    cleanup_expired_data()
    room = ChatRoom.query.get_or_404(room_id)
    current_user_id = session["user_id"]

    # 채팅방별 최대 참여 인원(6명) 제한 확인
    participants = room_participants.get(room_id, {})
    unique_user_ids = {info["user_id"] for info in participants.values()}
    if current_user_id not in unique_user_ids and len(unique_user_ids) >= MAX_ROOM_PARTICIPANTS:
        flash(
            f"'{room.title}' 채팅방의 최대 참여 인원({MAX_ROOM_PARTICIPANTS}명)이 초과되어 입장할 수 없습니다.",
            "danger",
        )
        return redirect(url_for("lobby"))

    # DB에 저장된 공지사항 불러오기 (채팅방별 최대 1개)
    active_notice = (
        Notice.query.filter_by(room_id=room_id)
        .order_by(Notice.created_at.desc())
        .first()
    )
    all_notices = [active_notice] if active_notice else []

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

    total_storage = get_total_uploaded_size()
    board_posts = BoardPost.query.order_by(BoardPost.updated_at.desc(), BoardPost.id.desc()).all()
    max_bytes = app.config.get("MAX_CONTENT_LENGTH", MAX_UPLOAD_SIZE_BYTES)
    return render_template(
        "room.html",
        room=room,
        active_notice=active_notice,
        all_notices=all_notices,
        messages=messages,
        files=files,
        client_ip=get_client_ip(),
        max_upload_bytes=max_bytes,
        max_upload_label=format_file_size_limit(max_bytes),
        total_storage_bytes=total_storage,
        total_storage_label=format_file_size_limit(total_storage),
        max_storage_label=format_file_size_limit(MAX_TOTAL_STORAGE_BYTES),
        board_posts=board_posts,
        max_participants=MAX_ROOM_PARTICIPANTS,
    )


# ==========================================
# HTTP Routes - File Upload / Download & DB History API
# ==========================================

@app.route("/room/<int:room_id>/upload", methods=["POST"])
@login_required
def upload_file(room_id: int):
    cleanup_expired_data()
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

    # 전체 파일 용량(최대 1GB) 초과 여부 검사
    current_total = get_total_uploaded_size()
    if current_total + file_size > MAX_TOTAL_STORAGE_BYTES:
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                pass
        available = max(0, MAX_TOTAL_STORAGE_BYTES - current_total)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        f"전체 업로드 파일 저장 용량(최대 1GB)을 초과하여 업로드할 수 없습니다. "
                        f"(현재 사용량: {format_file_size_limit(current_total)} / 1GB, 남은 용량: {format_file_size_limit(available)}). "
                        "기존 업로드 파일을 삭제한 후 다시 시도해주세요."
                    ),
                }
            ),
            400,
        )

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

    new_total = get_total_uploaded_size()
    new_total_label = format_file_size_limit(new_total)

    payload = msg.to_dict()
    payload["mime_type"] = uploaded.mime_type
    payload["download_url"] = url_for("download_file", file_id=uploaded.id)
    payload["total_storage_label"] = new_total_label

    # 실시간 전송 (전체 또는 특정 User)
    if target_user_id:
        for sid, info in room_participants.get(room.id, {}).items():
            if info["user_id"] in (session["user_id"], target_user_id):
                socketio.emit("new_message", payload, to=sid)
    else:
        socketio.emit("new_message", payload, to=f"room_{room.id}")

    # 전체 접속자에게 저장 용량 업데이트 브로드캐스트
    socketio.emit(
        "storage_updated",
        {
            "total_bytes": new_total,
            "total_label": new_total_label,
            "max_label": "1GB",
        },
    )

    return jsonify({"ok": True, "message": payload})


@app.route("/room/<int:room_id>/file/<int:file_id>/delete", methods=["POST"])
@login_required
def delete_file_route(room_id: int, file_id: int):
    ok, err = perform_delete_file(
        file_id=file_id,
        user_id=session["user_id"],
        user_name=session["user_name"],
        room_id=room_id,
    )
    if not ok:
        return jsonify({"ok": False, "error": err}), 404
    return jsonify({"ok": True, "total_label": format_file_size_limit(get_total_uploaded_size())})


@app.route("/download/<int:file_id>")
@login_required
def download_file(file_id: int):
    cleanup_expired_data()
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
    cleanup_expired_data()
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
    cleanup_expired_data()
    current_user_id = session["user_id"]
    notice = (
        Notice.query.filter_by(room_id=room_id)
        .order_by(Notice.created_at.desc())
        .first()
    )
    notices = [notice] if notice else []
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

@app.route("/api/room/<int:room_id>/polls")
@login_required
def api_room_polls(room_id: int):
    """채팅방 내 모든 투표 목록 조회 (진행 중 / 마감된 투표)"""
    cleanup_expired_data()
    room = ChatRoom.query.get_or_404(room_id)
    current_uid = session.get("user_id")
    polls = ChatPoll.query.filter_by(room_id=room.id).order_by(ChatPoll.created_at.desc()).all()
    for p in polls:
        p.check_is_closed()
    return jsonify({
        "ok": True,
        "polls": [p.to_dict(current_user_id=current_uid) for p in polls]
    })


@app.route("/api/room/<int:room_id>/poll/create", methods=["POST"])
@login_required
def api_create_poll(room_id: int):
    cleanup_expired_data()
    room = ChatRoom.query.get_or_404(room_id)
    user_id = session.get("user_id")
    user_name = session.get("user_name")

    data = request.get_json(silent=True) or request.form
    title = (data.get("title") or "").strip()
    is_multiple = str(data.get("is_multiple")).lower() in ("true", "1", "on")
    raw_deadline = (data.get("deadline") or "").strip()
    raw_options = data.get("options") or request.form.getlist("options")

    if not title:
        return jsonify({"ok": False, "error": "투표 제목을 입력해주세요."}), 400

    options_list = []
    if isinstance(raw_options, list):
        options_list = [str(o).strip() for o in raw_options if str(o).strip()]
    elif isinstance(raw_options, str):
        options_list = [o.strip() for o in raw_options.split("\n") if o.strip()]

    if len(options_list) < 2:
        return jsonify({"ok": False, "error": "투표 항목은 최소 2개 이상 입력해야 합니다."}), 400

    if not raw_deadline:
        return jsonify({"ok": False, "error": "투표 마감 시간을 입력해주세요."}), 400

    try:
        if "T" in raw_deadline:
            deadline_dt = datetime.fromisoformat(raw_deadline)
        else:
            deadline_dt = datetime.strptime(raw_deadline, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            deadline_dt = datetime.strptime(raw_deadline, "%Y-%m-%d %H:%M")
        except Exception:
            return jsonify({"ok": False, "error": "올바른 날짜/시간 형식이 아닙니다 (예: 2026-11-01 18:00)."}), 400

    if deadline_dt <= datetime.now():
        return jsonify({"ok": False, "error": "투표 마감 시간은 현재 시각보다 미래여야 합니다."}), 400

    new_poll = ChatPoll(
        room_id=room.id,
        creator_id=user_id,
        creator_name=user_name,
        title=title,
        is_multiple=is_multiple,
        deadline=deadline_dt,
        is_closed=False,
        created_at=datetime.now(),
    )
    db.session.add(new_poll)
    db.session.flush()

    for opt_text in options_list:
        opt = ChatPollOption(poll_id=new_poll.id, option_text=opt_text)
        db.session.add(opt)

    choice_str = "복수 선택" if is_multiple else "단수 선택"
    sys_msg = Message(
        room_id=room.id,
        sender_id="SYSTEM",
        sender_name="투표 알림",
        msg_type="system",
        content=f"📊 {user_name}({user_id})님이 새 투표를 등록했습니다: '{title}' ({choice_str} | 마감: {deadline_dt.strftime('%m-%d %H:%M')})",
    )
    db.session.add(sys_msg)
    db.session.commit()

    room_key = f"room_{room.id}"
    poll_payload = new_poll.to_dict(current_user_id=user_id)
    socketio.emit("poll_created", {"room_id": room.id, "poll": poll_payload}, to=room_key)
    socketio.emit("new_message", sys_msg.to_dict(), to=room_key)

    return jsonify({"ok": True, "poll": poll_payload})


@app.route("/api/room/<int:room_id>/poll/<int:poll_id>/vote", methods=["POST"])
@login_required
def api_cast_vote(room_id: int, poll_id: int):
    cleanup_expired_data()
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    poll = ChatPoll.query.filter_by(id=poll_id, room_id=room_id).first_or_404()

    if poll.check_is_closed():
        return jsonify({"ok": False, "error": "투표가 이미 마감되어 참여할 수 없습니다."}), 400

    data = request.get_json(silent=True) or request.form
    raw_opt_ids = data.get("option_ids") or request.form.getlist("option_ids")
    if not raw_opt_ids:
        return jsonify({"ok": False, "error": "투표할 항목을 최소 1개 이상 선택해주세요."}), 400

    option_ids = []
    if isinstance(raw_opt_ids, list):
        option_ids = [int(x) for x in raw_opt_ids if str(x).isdigit()]
    elif str(raw_opt_ids).isdigit():
        option_ids = [int(raw_opt_ids)]

    if not option_ids:
        return jsonify({"ok": False, "error": "유효한 투표 항목을 선택해주세요."}), 400

    if not poll.is_multiple and len(option_ids) > 1:
        return jsonify({"ok": False, "error": "단수 선택 투표는 1개 항목만 선택할 수 있습니다."}), 400

    valid_opt_ids = {opt.id for opt in poll.options}
    for oid in option_ids:
        if oid not in valid_opt_ids:
            return jsonify({"ok": False, "error": "유효하지 않은 투표 항목이 포함되어 있습니다."}), 400

    ChatPollVote.query.filter_by(poll_id=poll.id, user_id=user_id).delete()
    for oid in option_ids:
        vote = ChatPollVote(
            poll_id=poll.id,
            option_id=oid,
            user_id=user_id,
            user_name=user_name,
            created_at=datetime.now(),
        )
        db.session.add(vote)
    db.session.commit()

    poll_payload = poll.to_dict(current_user_id=user_id)
    room_key = f"room_{room_id}"
    socketio.emit("poll_updated", {"room_id": room_id, "poll": poll_payload}, to=room_key)

    return jsonify({"ok": True, "poll": poll_payload})


@app.route("/api/room/<int:room_id>/poll/<int:poll_id>/close", methods=["POST"])
@login_required
def api_close_poll(room_id: int, poll_id: int):
    cleanup_expired_data()
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    poll = ChatPoll.query.filter_by(id=poll_id, room_id=room_id).first_or_404()

    poll.is_closed = True
    db.session.commit()

    poll_dict = poll.to_dict(current_user_id=user_id)
    winners = [opt["option_text"] for opt in poll_dict["options"] if opt.get("is_winner")]
    winner_str = f"최다 득표: {', '.join(winners)}" if winners else "투표자 없음"

    sys_msg = Message(
        room_id=room_id,
        sender_id="SYSTEM",
        sender_name="투표 알림",
        msg_type="system",
        content=f"🏁 '{poll.title}' 투표가 마감되었습니다! 최종 결과 득표가 확정되었습니다 ({winner_str}).",
    )
    db.session.add(sys_msg)
    db.session.commit()

    room_key = f"room_{room_id}"
    socketio.emit("poll_updated", {"room_id": room_id, "poll": poll_dict, "closed": True}, to=room_key)
    socketio.emit("new_message", sys_msg.to_dict(), to=room_key)

    return jsonify({"ok": True, "poll": poll_dict})


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
            "max_users": MAX_ROOM_PARTICIPANTS,
            "users": user_list,
            "active_sharer": sharer,
        },
        to=f"room_{room_id}",
    )


@socketio.on("join_lobby")
def handle_join_lobby():
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    session_token = session.get("session_token")
    if not user_id or not session_token or session.get("boot_id") != SERVER_BOOT_ID:
        emit("auth_error", {"message": "로그인이 필요합니다."})
        return

    ip_addr = get_client_ip()
    ok, reason = check_and_Touch_ip_session(ip_addr, user_id, session_token)
    if not ok:
        emit("auth_error", {"message": reason})
        return

    lobby_participants[request.sid] = {
        "user_id": user_id,
        "name": user_name,
        "ip": ip_addr,
    }


@socketio.on("join_chat_room")
def handle_join_chat_room(data):
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    session_token = session.get("session_token")
    if not user_id or not session_token or session.get("boot_id") != SERVER_BOOT_ID:
        emit("auth_error", {"message": "로그인이 필요합니다."})
        return

    ip_addr = get_client_ip()
    ok, reason = check_and_Touch_ip_session(ip_addr, user_id, session_token)
    if not ok:
        emit("auth_error", {"message": reason})
        return

    room_id = int(data.get("room_id", 0))
    if room_id not in room_participants:
        room_participants[room_id] = {}

    unique_user_ids = {info["user_id"] for info in room_participants[room_id].values()}
    if user_id not in unique_user_ids and len(unique_user_ids) >= MAX_ROOM_PARTICIPANTS:
        emit(
            "room_full_error",
            {
                "message": f"채팅방 최대 참여 인원({MAX_ROOM_PARTICIPANTS}명)이 초과되어 입장할 수 없습니다."
            },
        )
        return

    room_key = f"room_{room_id}"
    join_room(room_key)

    already_in_room = user_id in unique_user_ids

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

    cleanup_expired_data()

    room_id = int(data.get("room_id", 0))
    raw_content = (data.get("content") or "").replace("\r\n", "\n")
    if not raw_content.strip():
        return
    content = raw_content.strip("\n")
    target_user_id = (data.get("target_user_id") or "").strip() or None
    target_user_name = (data.get("target_user_name") or "").strip() or None

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


@socketio.on("clear_room_chat")
def handle_clear_room_chat(data):
    """현재 채팅방의 모든 대화(메시지, 업로드 파일)를 초기화합니다."""
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    room = ChatRoom.query.get(room_id)
    if not room:
        return

    # 해당 채팅방의 업로드 파일 실제 파일 및 DB 삭제
    files = UploadedFile.query.filter_by(room_id=room_id).all()
    for f in files:
        fpath = os.path.join(UPLOAD_DIR, f.stored_filename)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass
        db.session.delete(f)

    # 해당 채팅방의 메시지 삭제
    Message.query.filter_by(room_id=room_id).delete(synchronize_session=False)
    ChatPoll.query.filter_by(room_id=room_id).delete(synchronize_session=False)

    # 초기화 안내 시스템 메시지 등록
    sys_msg = Message(
        room_id=room_id,
        sender_id="SYSTEM",
        sender_name="시스템",
        msg_type="system",
        content=f"🧹 {user_name}({user_id})님이 채팅방 대화 내용을 초기화했습니다.",
    )
    db.session.add(sys_msg)
    db.session.commit()

    room_key = f"room_{room_id}"
    socketio.emit("chat_cleared", {"room_id": room_id, "sys_msg": sys_msg.to_dict()}, to=room_key)


@socketio.on("set_notice")
def handle_set_notice(data):
    """채팅방 공지사항 설정 및 DB 저장 (채팅방별 1개 유지, 기존 공지가 있으면 최신 내용으로 update)"""
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    content = (data.get("content") or "").strip()
    if not content:
        return

    existing_notices = (
        Notice.query.filter_by(room_id=room_id)
        .order_by(Notice.created_at.desc())
        .all()
    )

    if existing_notices:
        target_notice = existing_notices[0]
        target_notice.author_id = user_id
        target_notice.author_name = user_name
        target_notice.content = content
        target_notice.is_active = True
        target_notice.created_at = datetime.now()
        for extra in existing_notices[1:]:
            db.session.delete(extra)
        action_desc = "공지사항을 최신 내용으로 업데이트했습니다"
    else:
        target_notice = Notice(
            room_id=room_id,
            author_id=user_id,
            author_name=user_name,
            content=content,
            is_active=True,
        )
        db.session.add(target_notice)
        action_desc = "새 공지사항을 등록했습니다"

    sys_msg = Message(
        room_id=room_id,
        sender_id="SYSTEM",
        sender_name="공지 알림",
        msg_type="system",
        content=f"📢 {user_name}({user_id})님이 {action_desc}: {content}",
    )
    db.session.add(sys_msg)
    db.session.commit()

    room_key = f"room_{room_id}"
    socketio.emit(
        "notice_updated",
        {"action": "set", "notice": target_notice.to_dict()},
        to=room_key,
    )
    socketio.emit("new_message", sys_msg.to_dict(), to=room_key)


@socketio.on("delete_notice")
def handle_delete_notice(data):
    """채팅방 공지사항 삭제 처리 (채팅방별 1개 공지 삭제)"""
    user_id = session.get("user_id")
    user_name = session.get("user_name")
    if not user_id:
        return

    room_id = int(data.get("room_id", 0))
    notice_id = data.get("notice_id")

    Notice.query.filter_by(room_id=room_id).delete(synchronize_session=False)

    sys_msg = Message(
        room_id=room_id,
        sender_id="SYSTEM",
        sender_name="공지 알림",
        msg_type="system",
        content=f"🗑️ {user_name}({user_id})님이 공지사항을 삭제했습니다.",
    )
    db.session.add(sys_msg)
    db.session.commit()

    room_key = f"room_{room_id}"
    socketio.emit(
        "notice_updated",
        {
            "action": "delete",
            "deleted_id": notice_id,
            "notice": None,
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


def _delayed_logout_if_disconnected(user_id: str, ip_addr: str):
    socketio.sleep(DISCONNECT_GRACE_SECONDS)
    with app.app_context():
        if not is_user_connected_via_socket(user_id):
            logout_user_ip_session(user_id=user_id, ip_addr=ip_addr)


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    disconnected_users = set()

    if sid in lobby_participants:
        lobby_info = lobby_participants.pop(sid)
        disconnected_users.add((lobby_info["user_id"], lobby_info["ip"]))

    for room_id, members in list(room_participants.items()):
        if sid in members:
            leaving_info = members.pop(sid)
            uid = leaving_info["user_id"]
            uname = leaving_info["name"]
            uip = leaving_info["ip"]
            disconnected_users.add((uid, uip))
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

    for uid, uip in disconnected_users:
        if not is_user_connected_via_socket(uid):
            socketio.start_background_task(_delayed_logout_if_disconnected, uid, uip)


def init_db():
    with app.app_context():
        db.create_all()
        # 시스템 시작/초기화 시 이전 접속 세션을 모두 해제(Logout)하여 항상 메인 화면부터 시작하도록 보장
        ActiveIPSession.query.delete()
        db.session.commit()

        # 48시간 지난 업로드 파일 및 채팅 메시지 삭제 (공지사항 제외)
        cleanup_expired_data()

        # 채팅방별로 공지사항이 1개만 유지되도록 최신 공지 외 정리
        for r in ChatRoom.query.all():
            room_notices = (
                Notice.query.filter_by(room_id=r.id)
                .order_by(Notice.created_at.desc())
                .all()
            )
            if room_notices:
                room_notices[0].is_active = True
                for extra in room_notices[1:]:
                    db.session.delete(extra)
        db.session.commit()

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


        if BoardPost.query.count() == 0:
            welcome_post = BoardPost(
                title="OpenChatting 커뮤니티 게시판 오픈 안내",
                content=(
                    "OpenChatting 로비 게시판에 오신 것을 환영합니다!\n\n"
                    "1. 이 게시판은 공지사항 및 회원 간의 자유로운 소통 공간입니다.\n"
                    "2. 글 작성 시에는 글만 쓰거나, 그림(이미지) 1장과 함께 글을 작성할 수 있습니다.\n"
                    "3. 게시글의 수정 및 삭제는 해당 글을 등록한 회원 본인만 가능합니다.\n"
                    "4. 게시글 제목을 클릭하면 상세 내용과 첨부된 그림을 확인하실 수 있습니다."
                ),
                author_id="admin",
                author_name="관리자",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            db.session.add(welcome_post)
            db.session.commit()


init_db()

# `flask run` 명령어 실행 시에도 기본 포트(5000) 대신 4000번 포트로 열리도록 보장
try:
    import flask.cli as _flask_cli

    _orig_run_simple = _flask_cli.run_simple

    def _custom_run_simple(host, port, application, *args, **kwargs):
        if port == 5000 and os.environ.get("FLASK_RUN_PORT") != "5000":
            port = int(os.environ.get("PORT", 4000))
        if host == "127.0.0.1" and not os.environ.get("FLASK_RUN_HOST"):
            host = "0.0.0.0"
        return _orig_run_simple(host, port, application, *args, **kwargs)

    _flask_cli.run_simple = _custom_run_simple
except Exception:
    pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4000))
    print(f"Starting OpenChatting Flask Server on http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)

