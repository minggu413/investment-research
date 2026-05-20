from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import tempfile
import warnings
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning


SHEET_ID = "1XjcvUIKKES1QRtEUM0hhI8ACcCacqnnyYgoWGeGr1u8"
SHEET_GID = "2119247325"
SHEET_NAME = "종합(제조)"
DEFAULT_OUTPUT_DIR = Path(r"G:\내 드라이브\Chrome에서 저장됨")
DEFAULT_GOOGLE_FOLDER_ID = "1KHnSQGo8dbamDauUq9E88LZQAOpLUOSY"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp_no}"
NAVER_STOCK_URL = "https://stock.naver.com/domestic/stock/{code}"

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


@dataclass
class Disclosure:
    date: str
    time: str
    company: str
    report: str
    before_pct: float | None
    after_pct: float | None
    change_pct: float | None
    change_shares: float | None
    rcp_no: str
    dart_link: str


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_company(name: str) -> str:
    name = clean_text(name)
    aliases = {
        "NC": "엔씨소프트",
        "IPARK현대산업개발": "HDC현대산업개발",
        "금호석유화학": "금호석유",
    }
    name = aliases.get(name, name)
    name = re.sub(r"\s+IR$", "", name)
    name = name.replace("(주)", "").replace("주식회사", "").replace("㈜", "")
    return re.sub(r"[\s·ㆍ\-_]", "", name)


def parse_number(value: str) -> float | None:
    value = clean_text(value)
    if not value or value in {"-", "#N/A", "#VALUE!"}:
        return None
    value = value.replace(",", "").replace("%", "")
    value = value.replace("▲", "").replace("▼", "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def format_pct_point(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%p"


def request_get(url: str, timeout: int = 20) -> requests.Response:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    response.raise_for_status()
    return response


def default_label(submitter: str) -> str:
    submitter = clean_text(submitter)
    if "국민연금" in submitter:
        return "국민연금"
    if "트러스톤" in submitter:
        return "트러스톤"
    label = submitter
    for token in ("자산운용", "공단", "주식회사", "(주)", "㈜"):
        label = label.replace(token, "")
    return clean_text(label) or submitter


def collect_disclosure_candidates(days: int, submitter: str) -> list[dict[str, str]]:
    base = "https://dart.fss.or.kr/dsac001/mainO.do"
    items: dict[str, dict[str, str]] = {}
    submitter = clean_text(submitter)
    submitter_pattern = re.escape(submitter)

    for day in range(days + 1):
        url = f"{base}?mdayCnt={day}&series=asc&sort=rpt"
        try:
            html = request_get(url, timeout=15).text
        except Exception:
            continue

        soup = BeautifulSoup(html, "html.parser")
        for row in soup.find_all("tr"):
            raw_html = str(row)
            if submitter not in raw_html:
                continue
            rcp_match = re.search(r"openReportViewer\('(?P<rcp>\d+)'", raw_html)
            if not rcp_match:
                continue
            rcp_no = rcp_match.group("rcp")
            if rcp_no in items:
                continue

            raw = clean_text(row.get_text(" "))
            date_match = re.search(r"20\d{2}\.\d{2}\.\d{2}", raw)
            time_match = re.search(r"^\d{2}:\d{2}", raw)
            date = date_match.group(0) if date_match else ""
            tm = time_match.group(0) if time_match else ""

            company = ""
            report = ""
            report_pattern = (
                r"임원ㆍ주요주주특정증권등소유상황보고서|"
                r"주식등의대량보유상황보고서\(약식\)|"
                r"주식등의대량보유상황보고서"
            )
            match = re.search(
                rf"^\d{{2}}:\d{{2}}\s+\S+\s+(?P<company>.+?)\s+"
                rf"(?P<report>{report_pattern})\s+(?P<submitter>.*?{submitter_pattern}.*?)\s+20",
                raw,
            )
            if match:
                company = clean_text(match.group("company"))
                report = clean_text(match.group("report"))

            items[rcp_no] = {
                "date": date,
                "time": tm,
                "company": company,
                "report": report,
                "rcp_no": rcp_no,
                "dart_link": DART_VIEW_URL.format(rcp_no=rcp_no),
            }
        time.sleep(0.05)

    return sorted(items.values(), key=lambda x: (x["date"], x["time"]), reverse=True)


def fetch_document_xml(api_key: str, rcp_no: str) -> str:
    url = (
        "https://opendart.fss.or.kr/api/document.xml"
        f"?crtfc_key={api_key}&rcept_no={rcp_no}"
    )
    content = request_get(url, timeout=25).content
    if content[:2] != b"PK":
        text = content.decode("utf-8", errors="replace")
        raise RuntimeError(f"DART document response is not a zip file: {text[:300]}")
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")


def pick_share_and_pct(nums: list[float]) -> tuple[float, float] | None:
    if len(nums) < 2:
        return None
    for idx in range(len(nums) - 1):
        shares = nums[idx]
        pct = nums[idx + 1]
        if abs(shares) > 100 and abs(pct) <= 100:
            return shares, pct
    return nums[0], nums[1]


def extract_disclosure(api_key: str, item: dict[str, str]) -> Disclosure:
    xml = fetch_document_xml(api_key, item["rcp_no"])
    soup = BeautifulSoup(xml, "html.parser")

    company = item["company"]
    company_tag = soup.find("company-name")
    if not company and company_tag:
        company = clean_text(company_tag.get_text()).replace("(주)", "")

    before_shares = after_shares = before_pct = after_pct = None
    change_shares = change_pct = None

    for tr in soup.find_all("tr"):
        cells = [
            clean_text(cell.get_text(" "))
            for cell in tr.find_all(["td", "th", "te", "tu"])
        ]
        if not cells:
            continue
        joined = " | ".join(cells)
        nums = [parse_number(cell) for cell in cells]
        nums = [num for num in nums if num is not None]

        if re.search(r"^직전\s*보고서|^직전보고서", joined) and before_pct is None:
            picked = pick_share_and_pct(nums)
            if picked:
                before_shares, before_pct = picked
        elif re.search(r"^이번\s*보고서|^이번보고서", joined) and after_pct is None:
            picked = pick_share_and_pct(nums)
            if picked:
                after_shares, after_pct = picked
        elif re.search(r"^증\s*감", joined) and change_pct is None:
            picked = pick_share_and_pct(nums)
            if picked:
                change_shares, change_pct = picked

    if change_shares is None and before_shares is not None and after_shares is not None:
        change_shares = after_shares - before_shares
    if change_pct is None and before_pct is not None and after_pct is not None:
        change_pct = after_pct - before_pct

    return Disclosure(
        date=item["date"],
        time=item["time"],
        company=clean_text(company),
        report=item["report"],
        before_pct=before_pct,
        after_pct=after_pct,
        change_pct=change_pct,
        change_shares=change_shares,
        rcp_no=item["rcp_no"],
        dart_link=item["dart_link"],
    )


def download_sheet_csv(sheet_url: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", sheet_url)
    sheet_id = match.group(1) if match else SHEET_ID
    gid_match = re.search(r"gid=(\d+)", sheet_url)
    gid = gid_match.group(1) if gid_match else SHEET_GID
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    return request_get(export_url, timeout=30).content.decode("utf-8-sig")


def load_metrics(sheet_url: str, metrics_file: Path | None) -> dict[str, dict[str, str]]:
    if metrics_file:
        if metrics_file.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            df = pd.read_excel(metrics_file, sheet_name=SHEET_NAME, header=None, dtype=str)
        else:
            df = pd.read_csv(metrics_file, header=None, dtype=str, keep_default_na=False)
    else:
        csv_text = download_sheet_csv(sheet_url)
        df = pd.read_csv(io.StringIO(csv_text), header=None, dtype=str, keep_default_na=False)

    ytd_idx = 60  # BI fallback
    for row_idx in range(min(5, len(df))):
        for col_idx, value in enumerate(df.iloc[row_idx].tolist()):
            if clean_text(value).upper() == "YTD":
                ytd_idx = col_idx
                break

    metrics: dict[str, dict[str, str]] = {}
    for _, row in df.iterrows():
        company = clean_text(row.iloc[4] if len(row) > 4 else "")
        code = clean_text(row.iloc[3] if len(row) > 3 else "")
        if not company or company in {"기업명", "#N/A"}:
            continue
        key = normalize_company(company)
        if not key or key in metrics:
            continue
        code = re.sub(r"\D", "", code).zfill(6) if code else ""
        metrics[key] = {
            "종목코드": code,
            "시가총액": clean_text(row.iloc[13] if len(row) > 13 else ""),
            "PBR": clean_text(row.iloc[17] if len(row) > 17 else ""),
            "POR": clean_text(row.iloc[26] if len(row) > 26 else ""),
            "YTD": clean_text(row.iloc[ytd_idx] if len(row) > ytd_idx else ""),
        }
    return metrics


def make_tables(rows: list[Disclosure]) -> tuple[list[Disclosure], list[Disclosure]]:
    increased = [
        row
        for row in rows
        if row.before_pct is not None
        and row.after_pct is not None
        and (row.change_pct or 0) > 0
    ]
    first_reports = [
        row for row in rows if row.before_pct is None and row.after_pct is not None
    ]
    dedup: dict[str, Disclosure] = {}
    for row in first_reports:
        key = normalize_company(row.company)
        if key not in dedup:
            dedup[key] = row
    return increased, list(dedup.values())


def attach_metrics(rows: list[Disclosure], metrics: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        data = asdict(row)
        metric = metrics.get(normalize_company(row.company), {})
        code = metric.get("종목코드", "")
        data.update(
            {
                "종목코드": code,
                "시가총액": metric.get("시가총액", "-") or "-",
                "PBR": metric.get("PBR", "-") or "-",
                "POR": metric.get("POR", "-") or "-",
                "YTD": metric.get("YTD", "-") or "-",
                "네이버": NAVER_STOCK_URL.format(code=code) if code else "-",
            }
        )
        output.append(data)
    return output


def to_excel_rows(rows: list[dict[str, Any]], table_type: str) -> list[dict[str, Any]]:
    excel_rows = []
    for row in rows:
        if table_type == "increase":
            holding = f"{format_pct(row['before_pct'])} -> {format_pct(row['after_pct'])}"
            change = format_pct_point(row["change_pct"])
        else:
            holding = format_pct(row["after_pct"])
            change = ""
        excel_rows.append(
            {
                "공시일": row["date"],
                "회사명": row["company"],
                "지분율": holding,
                "증감": change,
                "종목코드": row["종목코드"],
                "시가총액": row["시가총액"],
                "PBR": row["PBR"],
                "POR": row["POR"],
                "YTD": row["YTD"],
                "네이버": f'=HYPERLINK("{row["네이버"]}","네이버")'
                if row["네이버"] != "-"
                else "-",
                "DART": f'=HYPERLINK("{row["dart_link"]}","DART")',
            }
        )
    return excel_rows


def print_markdown(title: str, rows: list[dict[str, Any]], table_type: str) -> None:
    print(f"\n## {title}\n")
    if table_type == "increase":
        headers = ["공시일", "회사명", "지분율 변화", "증감", "종목코드", "시가총액", "PBR", "POR", "YTD", "네이버", "DART"]
    else:
        headers = ["공시일", "회사명", "최초 신고 후 지분율", "종목코드", "시가총액", "PBR", "POR", "YTD", "네이버", "DART"]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        if table_type == "increase":
            values = [
                row["date"],
                row["company"],
                f"{format_pct(row['before_pct'])} -> {format_pct(row['after_pct'])}",
                format_pct_point(row["change_pct"]),
                row["종목코드"] or "-",
                row["시가총액"],
                row["PBR"],
                row["POR"],
                row["YTD"],
                row["네이버"],
                row["dart_link"],
            ]
        else:
            values = [
                row["date"],
                row["company"],
                format_pct(row["after_pct"]),
                row["종목코드"] or "-",
                row["시가총액"],
                row["PBR"],
                row["POR"],
                row["YTD"],
                row["네이버"],
                row["dart_link"],
            ]
        print("| " + " | ".join(str(v) for v in values) + " |")


def save_excel(
    output_dir: Path,
    increased_rows: list[dict[str, Any]],
    first_rows: list[dict[str, Any]],
    filename: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path = output_dir / (filename or f"국민연금_지분공시_{today}.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(to_excel_rows(increased_rows, "increase")).to_excel(writer, sheet_name="지분증가", index=False)
        pd.DataFrame(to_excel_rows(first_rows, "first")).to_excel(writer, sheet_name="신규공시", index=False)
    return path


def upload_as_google_sheet(source_file: Path, title: str, folder_id: str) -> str:
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError(
            "Google Sheets 업로드용 패키지가 없습니다. 다음 명령을 한 번 실행하세요:\n"
            "python -m pip install --user google-api-python-client google-auth google-auth-oauthlib"
        ) from exc

    scopes = ["https://www.googleapis.com/auth/drive.file"]
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    token_path = Path(__file__).with_name("token.json")
    client_secret_path = Path(__file__).with_name("credentials.json")

    if credentials_path:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=scopes
        )
    else:
        try:
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise RuntimeError(
                "OAuth 업로드용 패키지가 없습니다. 다음 명령을 한 번 실행하세요:\n"
                "python -m pip install --user google-api-python-client google-auth google-auth-oauthlib"
            ) from exc

        credentials = None
        if token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if not client_secret_path.exists():
                    raise RuntimeError(
                        "Google Drive 업로드 인증 파일이 없습니다.\n"
                        f"다음 위치에 OAuth 클라이언트 파일을 credentials.json 이름으로 저장하세요:\n"
                        f"{client_secret_path}\n"
                        "또는 서비스계정 JSON 경로를 GOOGLE_APPLICATION_CREDENTIALS 환경변수로 지정하세요."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
                credentials = flow.run_local_server(port=0)
            token_path.write_text(credentials.to_json(), encoding="utf-8")

    service = build("drive", "v3", credentials=credentials)
    media = MediaFileUpload(
        str(source_file),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )
    metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id],
    }
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="id, webViewLink", supportsAllDrives=True)
        .execute()
    )
    return created["webViewLink"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="주요 투자자 최근 1년 지분증가/신규공시 기업 조회")
    parser.add_argument("--days", type=int, default=365, help="조회 기간. 기본값: 365일")
    parser.add_argument(
        "--submitter",
        default="국민연금공단",
        help="DART 공시 제출자 키워드. 예: 국민연금공단, 트러스톤",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="출력 표와 저장 파일에 사용할 짧은 이름. 미지정 시 제출자명에서 자동 생성",
    )
    parser.add_argument("--dart-key", default=os.getenv("DART_API_KEY"), help="DART API 키")
    parser.add_argument(
        "--sheet-url",
        default=f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid={SHEET_GID}",
        help="종목코드/시가총액/PBR/POR/YTD를 가져올 구글시트 URL",
    )
    parser.add_argument("--metrics-file", type=Path, default=None, help="비공개 시트일 때 사용할 CSV/XLSX 파일")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--google-folder-id",
        default=DEFAULT_GOOGLE_FOLDER_ID,
        help="결과 Google Sheets를 저장할 Google Drive 폴더 ID",
    )
    parser.add_argument(
        "--no-google-upload",
        action="store_true",
        help="Google Sheets 업로드 없이 XLSX만 저장",
    )
    parser.add_argument(
        "--keep-xlsx",
        action="store_true",
        help="Google Sheets 업로드 후 임시 XLSX 파일도 보관",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.dart_key:
        print("DART API 키가 없습니다. DART_API_KEY 환경변수 또는 --dart-key를 지정하세요.", file=sys.stderr)
        return 2

    label = clean_text(args.label) if args.label else default_label(args.submitter)

    print(f"{label} 후보 공시 수집 중... 최근 {args.days}일")
    candidates = collect_disclosure_candidates(args.days, args.submitter)
    print(f"후보 공시: {len(candidates)}건")

    disclosures: list[Disclosure] = []
    for idx, item in enumerate(candidates, start=1):
        try:
            disclosures.append(extract_disclosure(args.dart_key, item))
        except Exception as exc:
            print(f"[경고] 원문 파싱 실패 {item['rcp_no']} {item['company']}: {exc}", file=sys.stderr)
        if idx % 10 == 0:
            print(f"원문 파싱: {idx}/{len(candidates)}")
        time.sleep(0.05)

    increased, first_reports = make_tables(disclosures)

    try:
        metrics = load_metrics(args.sheet_url, args.metrics_file)
    except Exception as exc:
        print(
            "[경고] 구글시트 지표를 불러오지 못했습니다. "
            "시트가 비공개이면 해당 탭을 CSV/XLSX로 내려받아 --metrics-file로 지정하세요.\n"
            f"원인: {exc}",
            file=sys.stderr,
        )
        metrics = {}

    increased_rows = attach_metrics(increased, metrics)
    first_rows = attach_metrics(first_reports, metrics)

    period_label = "최근 1년" if args.days == 365 else f"최근 {args.days}일"
    print_markdown(f"{label} {period_label} 지분증가 기업", increased_rows, "increase")
    print_markdown(f"{label} {period_label} 신규 공시 기업", first_rows, "first")

    today = datetime.now().strftime("%Y%m%d")
    title = f"{label}_지분공시_{today}"
    if args.no_google_upload:
        excel_path = save_excel(args.output_dir, increased_rows, first_rows, f"{title}.xlsx")
        print(f"\n엑셀 저장 완료: {excel_path}")
    else:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = save_excel(Path(temp_dir), increased_rows, first_rows, f"{title}.xlsx")
            try:
                sheet_url = upload_as_google_sheet(temp_path, title, args.google_folder_id)
            except RuntimeError as exc:
                print(f"\nGoogle 스프레드시트 저장 실패:\n{exc}", file=sys.stderr)
                print(
                    "\n임시 해결: XLSX만 저장하려면 다음 옵션으로 실행하세요:\n"
                    "python ps_disclosure_report.py --no-google-upload",
                    file=sys.stderr,
                )
                return 1
            print(f"\nGoogle 스프레드시트 저장 완료: {sheet_url}")
            if args.keep_xlsx:
                excel_path = save_excel(args.output_dir, increased_rows, first_rows, f"{title}.xlsx")
                print(f"임시 XLSX 보관 완료: {excel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
