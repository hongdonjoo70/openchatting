@echo off
chcp 65001 > nul
echo ===================================================
echo [OpenChatting] Python 가상환경(venv) 생성 및 패키지 설치
echo ===================================================

if not exist .venv (
    echo [1/3] .venv 가상환경을 생성합니다...
    python -m venv .venv
) else (
    echo [1/3] 기존 .venv 가상환경을 사용합니다.
)

echo [2/3] 가상환경 활성화 및 pip 업그레이드 중...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

echo [3/3] requirements.txt 패키지 다운로드 및 설치 중...
pip install -r requirements.txt

echo ===================================================
echo 설치가 완료되었습니다!
echo 실행 방법:
echo   call .venv\Scripts\activate.bat
echo   python app.py
echo ===================================================
pause

