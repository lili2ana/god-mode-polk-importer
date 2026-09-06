@dataclass
class FeedStage:
    name: str
    url: str
    table: str
    columns: list[str]
    conflict_key: list[str] = field(default_factory=lambda: ["parcel_id"])
    mapping_verified: bool = False
    stage_table: Optional[str] = None
    is_fixed_width: bool = False
    fixed_width_spec: Optional[list[tuple[int, int]]] = None
    delimiter: str = ","
    has_header: bool = True
    encoding: str = "utf-8"

    def __post_init__(self):
        if self.stage_table is None:
            self.stage_table = f"{self.table}_stage"


def _v2_names(base: str) -> tuple[str, str]:
    return f"polk_{base}_v2", f"polk_{base}_stage_v2"


FEEDS: dict[str, FeedStage] = {
    "owner": FeedStage(
        name="owner",
        url="ftps://ftp.polkflpa.gov/AppraisalData/ftp_owner.zip",
        table=_v2_names("owner")[0],
        stage_table=_v2_names("owner")[1],
        columns=["parcel_id", "ln_num", "name", "pctown", "mailto", "addr_1", "addr_2", "addr_3", "city", "state", "zip"],
        conflict_key=["parcel_id", "ln_num"],
        mapping_verified=True,
    ),
    "parcel": FeedStage(
        name="parcel",
        url="ftps://ftp.polkflpa.gov/AppraisalData/ftp_parcel.zip",
        table=_v2_names("parcel")[0],
        stage_table=_v2_names("parcel")[1],
        columns=[
            "parcel_id", "section", "township", "range", "sub", "parcel",
            "dorus_code", "dordesc", "dordesc1", "nh_cd", "nh_dscr",
            "homestead", "otherex", "excode", "exdesc", "port_val",
            "cls_lnd_val", "ag_class", "valuetype", "valuedesc",
            "tot_lnd_val", "tot_bld_val", "tot_xf_val", "totalval",
            "reconcile", "assessval", "taxval", "curtaxdist", "taxdist",
            "amtdue", "millrate", "yr_created", "yr_improved",
            "last_insp_dt", "tot_acreage", "pr_strap"
        ],
        conflict_key=["parcel_id"],
        mapping_verified=True,
    ),
    "sales": FeedStage(
        name="sales",
        url="https://www.polkflpa.gov/FTPPage/downloader.ashx?dir=%5CAppraisalData%5C&filename=ftp_sales.zip",
        table=_v2_names("sales")[0],
        stage_table=_v2_names("sales")[1],
        columns=["parcel_id", "sale_date", "sale_price", "deed_type", "grantor", "grantee"],
        conflict_key=["parcel_id", "sale_date", "deed_type"],
        mapping_verified=False,
    ),
    "legal": FeedStage(
        name="legal",
        url="ftps://ftp.polkflpa.gov/AppraisalData/ftp_legal.zip",
        table=_v2_names("legal")[0],
        stage_table=_v2_names("legal")[1],
        columns=["parcel_id", "num", "section", "township", "range", "sub", "parcel", "dscr"],
        conflict_key=["parcel_id", "num"],
        mapping_verified=True,
    ),
    "parcel-tax": FeedStage(
        name="parcel-tax",
        url="https://www.polkflpa.gov/FTPPage/downloader.ashx?dir=%5CAppraisalData%5C&filename=ftp_parceltax.zip",
        table=_v2_names("parcel_tax")[0],
        stage_table=_v2_names("parcel_tax")[1],
        columns=["parcel_id", "tax_year", "millage_rate", "taxes_levied", "exemptions"],
        conflict_key=["parcel_id", "tax_year"],
        mapping_verified=False,
    ),
    "permits": FeedStage(
        name="permits",
        url="https://www.polkflpa.gov/FTPPage/downloader.ashx?dir=%5CAppraisalData%5C&filename=ftp_permit.zip",
        table=_v2_names("permits")[0],
        stage_table=_v2_names("permits")[1],
        columns=["parcel_id", "permit_number", "permit_type", "issue_date", "status", "description"],
        conflict_key=["parcel_id", "permit_number"],
        mapping_verified=False,
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
