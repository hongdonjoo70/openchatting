# 💬 OpenChatting (Flask + Socket.IO 실시간 오픈채팅방)

Flask와 Flask-SocketIO, SQLite(SQLAlchemy) 기반으로 동작하는 실시간 오픈채팅 웹 애플리케이션입니다.

## ✨ 주요 기능

1. **Python 가상환경(`venv`) 패키지 설치 지원**
   - `requirements.txt`, `setup_venv.bat`(Windows), `setup_venv.sh`(Linux/macOS) 제공
2. **Docker 빌드 및 실행 지원**
   - `Dockerfile`, `.dockerignore`, `docker-compose.yml` 포함 (기본 포트 `4000`)
3. **회원가입 / 로그인 / 로그아웃**
   - `User ID`, `Password`, `이름(Name)`을 입력받아 회원가입 및 DB 저장
   - 로그인 시 **`User ID 저장`** 체크박스 지원 (쿠키 및 브라우저 `localStorage` 연동)
4. **동일 IP당 1명의 User 접속 제한**
   - 동일한 클라이언트 IP에서는 동시에 1개의 User 계정만 로그인 및 접속 가능하도록 제어 (`ActiveIPSession` 테이블 기반)
   - 기존 사용자가 로그아웃하거나 세션을 해제하면 다른 계정으로 접속 가능
5. **오픈채팅방 개설 및 접속**
   - 채팅방 로비에서 새 채팅방 개설 및 실시간 접속 인원 확인 후 입장
6. **채팅방 기능**
   - **메시지 전송**: **전체 전송** 및 **특정 User에게만 전송(귓속말)** 지원
   - **채팅방 공지사항 설정 및 삭제**: 공지사항 등록/변경/삭제 및 실시간 반영
   - **채팅방 접속자 List 출력**: 우측 사이드바에 실시간 접속자 목록 표시 및 1:1 전송/창 공유 바로가기 버튼 제공
   - **특정 Window 화면 공유 및 취소**: 브라우저 `getDisplayMedia({ video: { displaySurface: 'window' } })`를 사용해 특정 애플리케이션 창(Window)을 선택하여 방 전체 또는 특정 User에게 실시간 공유 및 취소(중지) 가능
   - **DB 영구 저장 및 조회**: 모든 공지사항과 메시지(전체/귓속말/파일)를 SQLite DB(`data/openchat.db`)에 저장하고 채팅방 내 **`DB 저장내역 보기`** 모달에서 검색 및 조회 가능
   - **파일 업로드 / 다운로드**: 채팅방에서 파일 첨부 업로드(`uploads/`) 및 다운로드 지원 (전체 또는 특정 User에게 파일 전송 가능)

---

## 🚀 1. Python Virtual Environment (`venv`) 설정 및 실행 방법

### Windows (PowerShell / CMD)

자동 설치 스크립트 실행:

```bat
setup_venv.bat
```

또는 수동 명령어 실행:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

### Linux / macOS

```bash
chmod +x setup_venv.sh
./setup_venv.sh
source .venv/bin/activate
python app.py
```

서버 실행 후 브라우저에서 **`http://localhost:4000`** 으로 접속합니다.

---

## 🐳 2. Docker 빌드 및 실행 방법

### Docker 단독 빌드 및 실행

```bash
docker build -t openchatting:latest .
docker run -d -p 4000:4000 --name openchatting-app openchatting:latest
```

### Docker Compose 실행

```bash
docker compose up --build -d
```

실행 후 **`http://localhost:4000`** 에서 서비스를 이용할 수 있습니다.
