export type UserRole =
  | "SUPER_ADMIN"
  | "ORG_ADMIN"
  | "SITE_MANAGER"
  | "SECURITY_GUARD"
  | "VIEWER";

export type Direction = "ENTRY" | "EXIT" | "BOTH";
export type CameraStatus = "ONLINE" | "OFFLINE" | "UNKNOWN" | "ERROR" | "CONNECTING";
export type VisitStatus =
  | "CURRENTLY_INSIDE"
  | "COMPLETED"
  | "EXIT_WITHOUT_MATCH"
  | "MANUALLY_RESOLVED";

export interface UserPublic {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  organization_id: string | null;
  is_active: boolean;
  site_ids: string[];
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserPublic;
}

export interface EventItem {
  id: string;
  organization_id: string;
  site_id: string;
  gate_id: string;
  camera_id: string;
  vehicle_id: string | null;
  visit_id: string | null;
  direction: Direction;
  plate_text: string;
  raw_ocr_text: string;
  plate_normalized: string;
  ocr_confidence: number;
  plate_detection_confidence: number;
  vehicle_detection_confidence: number;
  timestamp: string;
  local_timestamp: string;
  snapshot_path: string | null;
  plate_crop_path: string | null;
  vehicle_crop_path: string | null;
  processing_duration_ms: number;
  source_type: string;
  sync_status: string;
  classification: string | null;
  notes: string | null;
  camera_name: string | null;
  gate_name: string | null;
  site_name: string | null;
  duplicate_suppressed?: boolean;
}

export interface Paginated<T> {
  items: T[];
  meta: { total: number; page: number; page_size: number };
}

export interface DashboardSummary {
  entries_today: number;
  exits_today: number;
  currently_inside: number;
  detections_today: number;
  cameras_active: number;
  cameras_offline: number;
  cameras_total: number;
  timezone: string;
  recent_events: EventItem[];
}

export interface CameraItem {
  id: string;
  organization_id: string;
  site_id: string;
  gate_id: string;
  name: string;
  camera_code: string;
  direction: Direction;
  onvif_ip: string | null;
  stream_type: string;
  resolution: string;
  enabled: boolean;
  streaming: boolean;
  status: CameraStatus;
  last_heartbeat: string | null;
  fps: number | null;
  connection_error: string | null;
  last_frame_at: string | null;
  retry_count: number;
  credentials_configured: boolean;
  rtsp_configured: boolean;
  site_name: string | null;
  gate_name: string | null;
}

export interface SiteItem {
  id: string;
  organization_id: string;
  name: string;
  address: string;
  timezone: string;
  settings: Record<string, unknown>;
  is_active: boolean;
}

export interface GateItem {
  id: string;
  organization_id: string;
  site_id: string;
  name: string;
  mode: "ENTRY" | "EXIT" | "MIXED";
}

export interface OrganizationItem {
  id: string;
  name: string;
  slug: string;
  retention_days: number;
  is_active: boolean;
}

export interface VehicleItem {
  id: string;
  organization_id: string;
  plate_normalized: string;
  first_seen: string;
  last_seen: string;
  total_visits: number;
  currently_inside: boolean;
  visitor_note: string | null;
  classification: string | null;
}

export interface VisitItem {
  id: string;
  organization_id: string;
  site_id: string;
  vehicle_id: string;
  plate_normalized: string;
  entry_event_id: string | null;
  exit_event_id: string | null;
  entry_at: string | null;
  exit_at: string | null;
  gate_id: string | null;
  duration_seconds: number | null;
  status: VisitStatus;
  duration_label: string | null;
}

export interface VehicleDetail {
  vehicle: VehicleItem;
  visits: VisitItem[];
  events: EventItem[];
}

export interface ReportResponse {
  kind: string;
  from_date: string;
  to_date: string;
  rows: { label: string; entries: number; exits: number; detections: number }[];
}
