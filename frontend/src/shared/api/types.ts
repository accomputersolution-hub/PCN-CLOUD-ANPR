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

export type VehicleRegistryCategory = "resident" | "guest" | "staff" | "vendor";

export interface RegistryMatch {
  known: boolean;
  status: VehicleRegistryCategory | "unknown" | string;
  /** Events display: active | inactive | unknown (live resolve, not stored on event). */
  registry_status?: "active" | "inactive" | "unknown" | string;
  plate_normalized: string;
  person_name?: string | null;
  flat_room_unit?: string | null;
  mobile_number?: string | null;
  registration_id?: string | null;
  active?: boolean | null;
  category?: string | null;
}

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
  operator_user_id?: string | null;
  camera_name: string | null;
  gate_name: string | null;
  site_name: string | null;
  duplicate_suppressed?: boolean;
  registry_match?: RegistryMatch | null;
}

export interface ManualAnprDetection {
  vehicle_id?: number | string | null;
  track_id?: string | null;
  vehicle_bbox?: number[];
  plate_bbox?: number[];
  plate: string;
  raw_ocr?: string;
  ocr_confidence: number;
  plate_confidence: number;
  confidence?: number;
  combined_confidence: number;
  is_primary?: boolean;
  matches_indian_pattern?: boolean;
  /** Per-detection plate crop; preferred over top-level primary crop for preview. */
  plate_crop_jpeg_base64?: string | null;
}

export interface AnprRoi {
  enabled: boolean;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ManualAnprAnalyzeResult {
  capture_id: string;
  organization_id: string;
  site_id: string;
  camera_id: string;
  vehicle_detected: boolean;
  plate_detected: boolean;
  detected_plate: string;
  raw_ocr: string;
  normalized_plate: string;
  ocr_confidence: number;
  plate_confidence: number;
  combined_confidence: number;
  matches_indian_pattern: boolean;
  ocr_confident: boolean;
  processing_ms: number;
  bbox: number[];
  plate_crop_jpeg_base64: string | null;
  error: string | null;
  event_created: boolean;
  plate_detector_mode?: string;
  plate_detector_used?: string;
  ai_detector_note?: string | null;
  plate_candidates?: Array<Record<string, unknown>>;
  selected_bbox?: number[];
  detections?: ManualAnprDetection[];
  detection_count?: number;
  vehicle_results?: Array<Record<string, unknown>>;
  anpr_debug?: Record<string, unknown>;
  anpr_roi_enabled?: boolean;
  anpr_roi?: AnprRoi | null;
  roi_vehicles?: number | null;
  ignored_outside_roi?: number | null;
  total_yolo_vehicles?: number | null;
  registry_match?: RegistryMatch | null;
}

export interface VehicleRegistryItem {
  id: string;
  organization_id: string;
  site_id: string;
  plate_normalized: string;
  vehicle_id?: string | null;
  category: VehicleRegistryCategory | string;
  person_name?: string | null;
  mobile_number?: string | null;
  flat_room_unit?: string | null;
  notes?: string | null;
  active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
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
  source_type?: string;
  nvr_id?: string | null;
  channel?: string | null;
  anpr_enabled?: boolean;
  gateway_id?: string | null;
  last_seen?: string | null;
  anpr_roi?: AnprRoi | null;
  anpr_calibration?: AnprCalibrationStored | null;
}

export interface CalibrationSummary {
  status: "GREEN" | "YELLOW" | "RED" | string;
  overall_score: number;
  component_scores: Record<string, number>;
  reasons: string[];
  guidance: string[];
  plate_width_px?: number | null;
  plate_height_px?: number | null;
  plate_text?: string | null;
  ocr_confidence?: number | null;
  brightness?: number | null;
  sharpness?: number | null;
  roi_enabled?: boolean;
  vehicle_in_roi?: boolean | null;
  processing_ms?: number;
}

export interface AnprCalibrationStored {
  updated_at: string;
  latest: CalibrationSummary;
  previous: CalibrationSummary | null;
}

export interface CameraCalibrateResult {
  camera_id: string;
  status: string;
  overall_score: number;
  component_scores: Record<string, number>;
  reasons: string[];
  guidance: string[];
  metrics: Record<string, unknown>;
  targets: Record<string, unknown>;
  overlays: {
    vehicle_bbox?: number[];
    plate_bbox?: number[];
    roi?: AnprRoi | null;
  };
  anpr_calibration: AnprCalibrationStored;
  previous: CalibrationSummary | null;
}

export interface SiteItem {
  id: string;
  organization_id: string;
  name: string;
  address: string;
  timezone: string;
  settings: Record<string, unknown>;
  is_active: boolean;
  connectivity_mode?: string;
  anpr_deployment_mode?: string;
  primary_gateway_id?: string | null;
}

export interface GatewayItem {
  id: string;
  organization_id: string;
  site_id: string;
  name: string;
  device_type: "EXISTING_VPN_ROUTER" | "PCN_CLOUD_GATEWAY";
  vendor: string;
  model: string;
  firmware_version: string | null;
  vpn_status: string;
  health_status: string;
  provisioning_status: string;
  last_seen: string | null;
  lan_subnet: string | null;
  is_active: boolean;
  last_error: string | null;
  config_version: number;
  camera_count: number;
  nvr_count: number;
  site_name: string | null;
  revoked: boolean;
}

export interface NvrItem {
  id: string;
  organization_id: string;
  site_id: string;
  gateway_id: string | null;
  name: string;
  vendor: string;
  model: string;
  host: string;
  channel_count: number;
  enabled: boolean;
  camera_count: number;
  site_name: string | null;
}

export interface SiteConnectivity {
  site_id: string;
  organization_id: string;
  site_name: string;
  connectivity_mode: "EXISTING_VPN_ROUTER" | "PCN_CLOUD_GATEWAY";
  anpr_deployment_mode: "LOCAL_EDGE_AGENT" | "CLOUD_VIA_GATEWAY";
  primary_gateway_id: string | null;
  gateway: GatewayItem | null;
  camera_count: number;
  nvr_count: number;
  edge_agent_count: number;
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
