# Termux 환경에서의 `lighter-sdk` 설치 가이드

Termux(Android) 환경에서는 암호화 모듈인 `cryptography` 및 `eth-account` 등이 포함되어 있어, 단순히 `pip install lighter-sdk`를 실행하면 컴파일러나 라이브러리 링크 오류로 빌드가 실패할 수 있습니다. 아래의 두 가지 해결책 중 하나를 사용해 대처해 보소서.

---

## 해결책 1. Termux 환경에 직접 빌드 라이브러리 설치 (권장)

Termux 컴파일러 환경을 온전히 세팅한 후 설치하는 방법입니다.

```bash
# 1. 시스템 및 패키지 업데이트
pkg update -y && pkg upgrade -y

# 2. 컴파일에 필요한 빌드 도구 및 라이브러리 설치
pkg install python clang make openssl libffi rust -y

# 3. 환경 변수 지정 (Rust 컴파일 오류 방지)
export CARGO_BUILD_TARGET=aarch64-linux-android

# 4. pip 업그레이드 및 필수 라이브러리 개별 설치 후 SDK 설치
pip install --upgrade pip
pip install cryptography
pip install lighter-sdk
```

---

## 해결책 2. Proot-Distro(Ubuntu)를 활용한 우회 방법 (가장 확실함)

만약 직접 빌드 시 지속적으로 컴파일 오류가 발생하는 경우, Termux 상에 경량화된 가상 우분투 환경을 구성하여 일반적인 리눅스 환경과 동일하게 구동하는 것이 가장 빠르고 완벽한 우회 방법입니다.

```bash
# 1. proot-distro 설치 및 우분투 환경 구성
pkg update -y
pkg install proot-distro -y
proot-distro install ubuntu

# 2. 우분투 환경으로 접속
proot-distro login ubuntu

# 3. 우분투 내부 개발 패키지 및 파이썬 설치
apt update && apt upgrade -y
apt install python3 python3-pip python3-venv git -y

# 4. 프로젝트 클론 및 가상환경 설정
git clone https://github.com/smartcall1/lightermoji.git
cd lightermoji
python3 -m venv venv
source venv/bin/activate

# 5. 패키지 설치 및 실행
pip install -r requirements.txt
python lighter_bot.py
```
> [!TIP]
> Termux에서 파이썬 패키지 충돌로 골머리를 앓으실 때는 **해결책 2 (Proot-distro)** 방식을 사용하는 것이 정신 건강에 이롭사옵니다.
