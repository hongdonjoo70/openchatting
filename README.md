# 💬 OpenChatting (Flask + Socket.IO 실시간 오픈채팅방)

Flask와 Flask-SocketIO, SQLite(SQLAlchemy) 기반으로 동작하는 실시간 오픈채팅 웹 애플리케이션입니다.

## ✨ 주요 기능

1. **Python 가상환경(`venv`) 패키지 설치 지원**
   - `requirements.txt`, `setup_venv.bat`(Windows), `setup_venv.sh`(Linux/macOS) 제공
2. **Docker 빌드 및 실행 지원**
   - `Dockerfile`, `.dockerignore`, `docker-compose.yml` 포함 (기본 포트 `4000`)
3. **간편 입장 (회원가입 없음 & IP 제한/정보 제거)**
   - 복잡한 회원가입 절차 없이, **이름(Name / 닉네임)**만 입력하여 원하는 채팅방으로 즉시 입장
   - **IP 접속 제한 및 IP 주소 노출이 완전히 제거**되어 동일 네트워크(공유기/NAT/로컬 환경)에서도 다수의 사용자가 자유롭게 동시 접속 가능
   - 접속 시 **`이름 저장`** 체크박스 지원 (브라우저 `localStorage` 연동)
4. **오픈채팅방 개설 및 접속**
   - 메인 오픈채팅방 바로 입장 또는 로비에서 새 채팅방 개설 (최대 6개, 방별 최대 6명)
   - 로비 및 채팅방 UI 전반에서 IP 정보 대신 사용자 이름(닉네임)만 깔끔하게 표시
5. **채팅방 핵심 기능**
   - **메시지 전송**: **전체 전송** 및 **특정 User에게만 전송(귓속말)** 지원
   - **채팅방 공지사항 설정 및 삭제**: 공지사항 등록/변경/삭제 (방별 최대 1개 유지) 및 실시간 동기화
   - **채팅방 접속자 List 출력**: 우측 사이드바에 실시간 접속자 목록(사용자 이름 표시) 및 귓속말/창 공유 바로가기 버튼 제공
   - **특정 Window 화면 공유 및 취소**: 브라우저 `getDisplayMedia`를 사용해 특정 애플리케이션 창(Window)을 선택하여 방 전체 또는 특정 User에게 실시간 공유 및 취소(중지) 가능
   - **DB 영구 저장 및 조회**: 모든 공지사항과 메시지(전체/귓속말/파일)를 SQLite DB에 저장하고 채팅방 내 **`DB 저장내역 보기`** 모달에서 검색 및 조회 가능
   - **파일 업로드 / 다운로드**: 채팅방 파일 첨부 업로드(개별 파일 최대 50MB, 전체 누적 1GB 한도) 및 다운로드 지원
   - **투표 기능**: 단수/복수 선택 및 마감 시간 설정 투표 개설, 실시간 참여 및 결과 확인
   - **로비 커뮤니티 게시판**: 글 및 그림(이미지 1장) 첨부 게시글 등록/수정/삭제 기능

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
