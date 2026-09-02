# 금융권 신규 프로젝트 공고 자동 알림

금융사 공고 게시판을 하루 1회 확인해, **새로 올라온 IT 프로젝트 공고만**
골라 **여러 담당자에게 일괄 메일 발송**하는 독립 실행 시스템입니다.
Claude 실행 여부와 무관하게 동작합니다.

---

## 0. 배포 방식 (서버 자동 갱신)

이 프로젝트는 GitHub 비공개 저장소에서 관리되며, **서버가 실행 전에 스스로 최신 코드를 받아옵니다.**
파일을 수동으로 복사할 필요가 없습니다.

```
개발 PC ──git push──> GitHub (private) <──git pull── 사내 서버(192.168.0.110)
                                                        └─ 매일 09:00 자동 실행
```

작업 스케줄러는 `run_watch.py` 가 아니라 **`update_and_run.ps1`** 을 호출합니다.
이 스크립트가 `git pull` → 의존 패키지 확인 → 감시 실행 순으로 진행합니다.

**`git pull` 이 실패해도 실행은 계속됩니다.** 네트워크 문제로 공고 조회까지 멈추면 안 되기 때문입니다.

서버에서 추적하지 않는 파일 (git 에 올라가지 않으므로 pull 해도 덮어써지지 않음):

| 파일 | 내용 |
|---|---|
| `.env` | SMTP 비밀번호 |
| `data/state.sqlite3` | 발송 이력 (중복 발송 방지의 근거) |
| `logs/` | 실행 로그 |

## 1. 5분 설치

### (1) 발송 계정 설정

```bash
copy .env.example .env
```

발신은 **@pron.co.kr (후이즈웍스)** 입니다. `.env` 에서 채울 것은 `SMTP_PASSWORD` 한 줄뿐이고,
나머지 값은 실측으로 확인해 미리 채워 두었습니다.

| 항목 | 값 | 확인 방법 |
|---|---|---|
| SMTP 서버 | `smtp.whoisworks.com` | pron.co.kr MX → `aspmx.whoisworks.com` |
| 포트 | `587` (STARTTLS) | 465는 닫혀 있음을 실측 확인 |
| 인증 | LOGIN / PLAIN | EHLO 응답으로 확인 |
| 계정 | `bjkim@pron.co.kr` | — |

> **알려진 서버 이슈 (해결 완료)**
> 후이즈웍스 SMTP는 Diffie-Hellman 파라미터가 1024비트라 Python 3.10+ 기본 보안수준에서
> `DH_KEY_TOO_SMALL` 오류로 TLS 협상이 거부됩니다. `mailer.py` 가 이를 감지해
> 보안수준을 낮춰 자동 재연결합니다. TLS 암호화 자체는 유지되며,
> 실측 결과 `TLSv1.2 / DHE-RSA-AES256-GCM-SHA384` 로 연결됩니다.

### (2) 수신자 등록

```bash
copy recipients.example.txt recipients.txt
```

`recipients.txt` 에 한 줄에 한 명씩 적습니다. **이 파일만 고치면 됩니다.**

```
김부장 <kimbj@pron.co.kr>
hong@pron.co.kr
lee@pron.co.kr
```

- `#` 로 시작하는 줄과 빈 줄은 무시됩니다
- `이름 <주소>` 형식과 주소만 적는 형식 모두 가능합니다
- 중복은 자동 제거됩니다
- 저장만 하면 다음 실행부터 적용됩니다. 재시작·재등록 불필요

> **`recipients.txt` 는 git 추적 대상이 아닙니다.**
> 서버에서 편집해도 `git pull` 이 깨지지 않고, 코드를 갱신해도 명단이 덮어써지지 않습니다.
> `.env` 와 같은 취급입니다.

> **최소 1명은 `config.yaml` 의 `mail.recipients` 에도 남겨 두십시오.**
> `recipients.txt` 가 없거나 비어도 발송이 0명이 되지 않게 하는 안전장치입니다.

### (3) 발송 경로 점검

```bash
python run_watch.py --test-mail
```

테스트 메일이 도착하면 발송 경로가 정상입니다.

### (4) 기준선 등록

```bash
python run_watch.py --seed
```

현재 게시판에 올라와 있는 공고를 "이미 본 것"으로 표시합니다.
**이 단계를 건너뛰면 첫 실행 때 과거 공고가 한꺼번에 발송됩니다.**

### (5) 스케줄러 등록

```powershell
powershell -ExecutionPolicy Bypass -File install_task.ps1
```

매일 09:00 자동 실행됩니다. 예약 시각에 PC가 꺼져 있었다면 부팅 직후 따라잡아 실행합니다.

---

## 2. 실행 명령

| 명령 | 동작 |
|---|---|
| `python run_watch.py` | 정상 실행 (신규 감지 → 메일 발송) |
| `python run_watch.py --dry-run` | 메일 없이 감지 결과만 확인 |
| `python run_watch.py --seed` | 현재 목록을 기준선으로 등록 |
| `python run_watch.py --force-mail` | 최초 실행이어도 즉시 발송 (시연용) |
| `python run_watch.py --test-mail` | SMTP 점검용 테스트 메일 |
| `python resend.py --days 1` | 최근 1일 이내 등록 공고를 **다시** 발송 |
| `python resend.py --days 1 --dry-run` | 재발송 대상만 확인 (발송 없음) |
| `python resend.py --seq kb:4918,hana:1528508` | 특정 공고만 지정 재발송 |

`resend.py` 는 **상태 파일을 건드리지 않습니다.** 재발송해도 발송 이력이 유지되므로
다음 정기 실행의 신규 판정에 영향이 없습니다. 서식을 바꿨을 때 새 형식으로 다시
돌려보거나, 수신자를 추가한 뒤 최근 공고를 공유할 때 씁니다.

---

## 2-1. 메일 형식

| 항목 | 값 |
|---|---|
| 발신자 | `PRON Project Searcher <bjkim@pron.co.kr>` |
| 제목 | `[프로엔솔루션] 신규 금융 프로젝트 공고 알림 N건` |
| 본문 | 기관별 그룹 → 공고당 **제목 · 공고번호 · 등록일 · 원문 링크** |

첨부 파일명은 넣지 않습니다. 서식은 메일 클라이언트 호환을 위해
`<style>` 블록 없이 **인라인 스타일 + table 레이아웃**으로만 작성합니다
(아웃룩·일부 웹메일이 `<head>` 의 `<style>` 을 제거합니다).

발신자명은 `.env` 의 `SMTP_FROM_NAME`, 제목 접두사는 `config.yaml` 의
`mail.subject_prefix` 로 바꿉니다.

### 발신 주소를 바꾸려면

메일 표준상 `From` 은 `표시명 <주소>` 형태이고 **주소는 반드시 포함됩니다.**
숨길 수는 없고, 어떤 주소를 보일지만 정할 수 있습니다.

```
SMTP_FROM_ADDR=noreply@pron.co.kr
```

비워두면 인증 계정(`SMTP_USER`)이 그대로 노출됩니다. 개인 주소를 감추려면
공용 주소를 발급받아 위 값에 넣으십시오.

> **주의**: 메일 서버가 인증 계정과 다른 `From` 을 허용해야 합니다.
> 후이즈웍스가 거부하면 발송이 실패하므로, 설정 후 반드시
> `python run_watch.py --test-mail` 로 확인하고 실패 시 값을 비우십시오.
> 인증 계정과 다른 주소로 보내면 SPF/DMARC 정책에 따라 스팸 처리될 수도 있습니다. **`.env` 는 git 추적 대상이 아니므로
발신자명 변경은 서버에서 따로 해야 합니다.**

## 2-2. 메일에 무엇을 담는가

`config.yaml` 의 `mail.attach_pdf` 로 정합니다.

| 설정 | 동작 |
|---|---|
| `false` (현재) | **원문 링크 + 첨부 파일명만.** 파일을 내려받지 않아 실행이 빠르고 용량 제한 문제가 없습니다 |
| `true` | 공고 PDF/HWP 원본을 메일에 첨부 (총 `max_attach_mb` 상한) |

첨부를 끄더라도 **어떤 문서가 붙어 있는지 파일명은 그대로 표시**되므로,
필요한 공고만 링크를 눌러 받으면 됩니다.

## 3. 감시 대상 기관

`config.yaml` 의 `sources` 목록으로 관리합니다. 기관을 껐다 켜려면 `enabled` 만 바꾸면 됩니다.

| id | 기관 | 수집 전략 | 첨부 |
|---|---|---|---|
| id | 기관 | 어댑터 | 첨부 |
|---|---|---|---|
| `kb` | KB국민은행 | `kb` | POST 폼 (PDF) |
| `nonghyup` | 농협 (은행·증권·손보·경제지주) | `nonghyup` | GET (ZIP) — 상세 재조회 필요 |
| `nhit` | 농협정보시스템 | `nhit` | GET |
| `nhfn` | NH농협금융지주 | `nhcms` | GET (HWP) |
| `nhab` | 농협경제지주 | `nhcms` | GET (HWP/PDF) |
| `hana` | 하나은행 | `hana` | GET (PDF) |
| `woorifg` | 우리금융그룹 | `woorifg` | GET (HWP) — 구형 TLS |
| `woorisb` | 우리금융저축은행 | `simpleboard` | 파일명만 (경로 확정 불가) |
| `shinhanfund` | 신한자산운용 | `simpleboard` | GET |

**기관 추가 방법**

1. `core/adapters/<기관>.py` 에 `BaseAdapter` 상속 클래스 작성
2. `core/adapters/__init__.py` 의 `REGISTRY` 에 한 줄 등록
3. `config.yaml` 의 `sources` 에 항목 추가

같은 CMS를 쓰는 기관이면 어댑터를 새로 만들지 않고 `params` 만 달리해 추가합니다
(`nhcms` 가 NH농협금융지주·농협경제지주를 함께 처리합니다).

**기관별 주의사항**

- **농협**: '해당 공고 상세를 방금 조회한 세션'에만 첨부를 내줍니다. 어댑터가 다운로드 직전 상세를 다시 엽니다.
- **우리금융그룹**: legacy renegotiation 미지원 서버라 전용 TLS 어댑터가 필요합니다.
- **우리금융저축은행**: 첨부가 `fileSn` 만 노출해 다운로드 경로를 확정할 수 없습니다.
  `params.attachments: false` 로 두어 **파일명만 메일에 표시**하고 다운로드는 건너뜁니다.
- **신한자산운용**: 페이징 파라미터가 동작하지 않아 `list_pages: 1` 입니다.
  게시판 대부분이 펀드 위탁운용사 공고라 IT 건은 드뭅니다.
- **skip_require**: 게시판 자체가 입찰공고 전용인 기관(농협 계열)은 1단계 게이트를 건너뜁니다.

## 3-1. 무엇을 "프로젝트 공고"로 보는가

`config.yaml` 의 `filter` 가 3단계로 판정합니다.

| 단계 | 동작 |
|---|---|
| 1. `require_keywords` | 공고/입찰/제안/RFP/RFI 중 하나도 없으면 탈락. 서비스 점검 안내 등을 걸러냄 |
| 2. `exclude_keywords` | 비IT·비금융 단어가 하나라도 있으면 탈락 |
| 3. `include_keywords` | IT/SI 단어가 하나라도 있으면 통과 |

9개 기관 실제 공고 220건으로 검증한 결과 **83건 통과** 입니다.

| | 예시 |
|---|---|
| 통과 | 코어뱅킹현대화 구축 제안요청, 포털시스템 구축, IBIS 안정화 사업, DR 멀티데이터센터 컨설팅, ISMS 인증 컨설팅, 그룹 데이터 통합·활용 인프라 구축 |
| 제외 | 소유부동산 매각, 축구 마케팅대행사, 서비스 일시중단 안내, 주주총회 소집공고, **x86서버 구매·네트워크 장비 교체·전산장비 통합도입 등 하드웨어 조달** |

## 4. "조회가 갑자기 막히면 안 된다"에 대한 설계

### 수집 전략 5단계 자동 강등

한 전략이 실패하면 다음 전략으로 내려갑니다. 어느 단계에서 성공했는지 로그에 남습니다.

1. `http-plain` — 표준 요청 (평시 경로)
2. `http-retry` — 재시도
3. `http-warmed` — 메인 페이지 선방문으로 쿠키 확보 후 조회 + UA 교체
4. `http-alt-url` — 다른 형태의 URL 파라미터로 우회
5. `browser-headless` — 실제 Chrome 엔진으로 조회 (JS 검증 도입 대응)

### 파싱 2단계 자동 강등

- `strict` — 현재 게시판 HTML 구조 기준 정밀 파싱
- `loose` — 구조가 개편되어도 `articleId` 링크와 날짜만 남아 있으면 복구

> 실제 검증: 테이블 마크업을 훼손시킨 시뮬레이션에서 `loose` 파서가 10건을 정상 복구했습니다.

### 조용히 죽지 않게 하는 장치

| 상황 | 동작 |
|---|---|
| 조회가 연속 2회 실패 | 경보 메일 발송 |
| 공고 파싱 0건 (구조 전면 개편) | 원본 HTML을 `data/` 에 보존 + 경보 메일 |
| 마지막 성공 후 30시간 경과 | 스케줄러 정지 의심 → 경보 메일 |
| 메일 일괄 발송 실패 | 수신자별 개별 발송으로 자동 강등 |
| 발송 도중 프로세스 중단 | '미발송'으로 기록되어 다음 실행에서 자동 재시도 |

### 한계 (정직하게)

KB가 **로그인 요구·캡차·WAF** 를 도입하면 무인 조회는 어떤 방법으로도 불가능해집니다.
이 시스템은 그 경우 **우회를 시도하지 않고 즉시 경보 메일을 보냅니다.**
"절대 막히지 않는다"가 아니라 **"막히면 반드시 즉시 알게 된다"** 가 이 설계의 보장 범위입니다.

---

## 5. 이중화 (선택)

정시성과 가용성을 더 올리려면 GitHub Actions를 병행합니다.
`.github/workflows/kb-watch.yml` 가 준비되어 있습니다.

- 저장소 Settings → Secrets → `SMTP_HOST` `SMTP_PORT` `SMTP_SECURITY` `SMTP_USER` `SMTP_PASSWORD` 등록
- 매 실행마다 `data/state.sqlite3` 를 커밋하므로 **60일 무활동 자동 비활성화**를 회피합니다

**주의할 점 3가지**

1. GitHub Actions의 `schedule` 은 정시 보장이 없습니다 (통상 5~30분 지연, 드물게 누락)
2. 러너가 미국 IP라 국내 금융권 사이트 응답이 다를 수 있습니다 — 실제로 돌려봐야 확인됩니다
3. 로컬 스케줄러와 병행할 경우 상태 파일을 공유해야 중복 발송이 없습니다

---

## 6. 구조

```
kb_notice_watcher/
├─ config.yaml          운영 설정 (주기·필터·발송 옵션)
├─ recipients.txt       수신자 명단  ← 이 파일만 고치면 됨
├─ .env                 SMTP 자격증명 (git 제외)
├─ run_watch.py         엔트리포인트
├─ install_task.ps1     Windows 작업 스케줄러 등록
├─ core/
│   ├─ settings.py      설정 로딩
│   ├─ fetcher.py       5단계 수집 전략
│   ├─ parser.py        2단계 파싱 전략
│   ├─ store.py         SQLite 상태 (중복 발송 방지·실패 추적)
│   ├─ render.py        메일 본문 생성
│   ├─ mailer.py        다중 수신자 SMTP 발송
│   └─ watcher.py       실행 오케스트레이션
├─ data/state.sqlite3   본 공고 + 실행 이력
└─ logs/watch.log       회전 로그 (2MB × 5개)
```

## 7. 점검

```bash
# 최근 실행 이력
python -c "import sqlite3;[print(r) for r in sqlite3.connect('data/state.sqlite3').execute('SELECT started_at,ok,strategy,parser,scanned,new_count,error FROM run_log ORDER BY id DESC LIMIT 10')]"

# 로그
type logs\watch.log
```
