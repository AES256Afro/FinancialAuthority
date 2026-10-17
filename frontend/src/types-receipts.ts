// Receipts and the files kept by month (M31): the shapes the server sends. Nothing here computes a figure.
import type { Status } from "./guide";
import type { DocumentRow, FactRow, PaystubRow } from "./types";

/** A kept file, with what M31 added. An older server (or the demo) leaves the new fields out. */
export interface KeptDocument extends DocumentRow {
  /** The month it is kept with, YYYY-MM; "" when it has none. */
  period?: string;
  merchant?: string;
  amount?: number | null;
  bought_on?: string | null;
  return_by?: string | null;
  warranty_until?: string | null;
  /** "upload", "mail:<id>", "import:<id>" or "import-folder". */
  came_from?: string;
  /** "ocr" when its text was read from a photo or a scan on the server (M49). */
  read_with?: string;
  /** A receipt kept without its total or day: what its words seem to say, to keep or not. */
  suggested?: { amount?: number; bought_on?: string } | null;
}

export interface KeptDocumentsOut {
  documents: KeptDocument[];
  kinds: string[];
}

/** GET /api/v1/documents/months: every file with a month, newest month first, and the files with none. */
export interface DocumentMonth {
  month: string;
  count: number;
  kinds: Record<string, number>;
  documents: KeptDocument[];
}

export interface DocumentMonthsOut {
  months: DocumentMonth[];
  undated: KeptDocument[];
}

/** A return window or a warranty still open, soonest first. */
export interface ReceiptDeadline {
  document_id: number;
  what: "return" | "warranty";
  on: string;
  days_left: number;
  status: Status;
  word: string;
  title: string;
  merchant: string;
  amount: number | null;
  attach_to: string;
  source: string;
}

export interface ReceiptRow {
  id: number;
  title: string;
  merchant: string;
  amount: number | null;
  bought_on: string | null;
  return_by: string | null;
  warranty_until: string | null;
  attach_to: string;
  period: string;
  source: string;
}

/** GET /api/v1/receipts */
export interface ReceiptsOut {
  as_of: string;
  deadlines: ReceiptDeadline[];
  receipts: ReceiptRow[];
  unmatched: ReceiptRow[];
  total: number;
  count: number;
  not_advice: string;
}

/** A purchase a receipt may belong to: the same total, within days of the day bought, the closest first. */
export interface ReceiptCandidate {
  id: number;
  date: string;
  merchant: string;
  amount: number;
  account: string;
  /** The store's name matches the purchase's. */
  named: boolean;
  /** The purchase already has a receipt kept with it. */
  has_receipt: boolean;
  source: string;
}

export interface CandidatesOut {
  candidates: ReceiptCandidate[];
}

/** A receipt read from mail, waiting on Needs you, with the purchases it may belong to. */
export interface ReceiptFields {
  merchant?: string;
  total?: number;
  order_no?: string;
  bought_on?: string;
  return_by?: string;
  warranty_until?: string;
  attachment?: string;
  sender?: string;
  subject?: string;
}

export type FactWithCandidates = FactRow & { candidates?: ReceiptCandidate[] };

/** A paystub with the file it came from, when the app kept it. */
export type PaystubWithFile = PaystubRow & { document_id?: number | null };

/** The reminder settings M31 added to GET/PUT /api/v1/push/reminders. */
export interface ReceiptReminders {
  /** Days before a receipt's last day to return; 0 is off. */
  returns_days: number;
  /** Days before a warranty ends; 0 is off. */
  warranty_days: number;
  /** The lease's written-notice deadline, three days before it. */
  notice: boolean;
}
