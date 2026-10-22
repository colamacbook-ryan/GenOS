/**
 * Phase-1 API types, hand-written from docs/api/phase-1.md.
 *
 * The shape deliberately mirrors what `openapi-typescript` emits
 * (`paths` + `components['schemas']`), so this file can be replaced by the
 * generated schema (`npx openapi-typescript http://api/openapi.json -o src/schema.ts`)
 * without touching the client: endpoints.ts only reads types through the
 * aliases at the bottom of this file. If the generated schema names differ,
 * only those aliases need updating.
 */

export interface components {
  schemas: {
    Problem: {
      type?: string;
      title?: string;
      status: number;
      code?: string;
      /** Either a human message, or an object (e.g. `{locked_until}` for PIN_LOCKED). */
      detail?: string | { locked_until?: string; [k: string]: unknown } | null;
      /** 401 PIN_INVALID */
      attempts_left?: number | null;
      /** 423 PIN_LOCKED (some servers put it top-level) */
      locked_until?: string | null;
      /** 422 field errors: `{ field: message }` */
      errors?: Record<string, string> | null;
      /** v0.1.30: 503 MODEL_UNAVAILABLE — lý do kỹ thuật từng nhà cung cấp (chuỗi). */
      reasons?: string[] | null;
    };
    Role: { code: string; name: string };
    Org: { id: string; name: string; timezone: string; currency: string };
    Addressing: { self: string; bot_calls_me: string };
    Me: {
      id: string;
      email: string;
      display_name: string;
      role: components['schemas']['Role'];
      org: components['schemas']['Org'];
      addressing: components['schemas']['Addressing'];
      pin_verified_until: string | null;
      permissions: Record<string, 'all' | 'scoped' | 'none' | string>;
      /** v0.1.19: mật khẩu do hệ thống đặt (genh reset-password) — Console buộc đặt mật khẩu mới. */
      must_change_password?: boolean;
      /** v0.1.21: tính năng bật cho người này (`gen` = khung chat Gen, cờ `gen.enabled`, v1 chỉ Owner). */
      features?: { gen?: boolean };
    };
    LoginRequest: { email: string; password: string };
    PinVerifyRequest: { pin: string };
    PinVerifyResponse: { pin_verified_until: string };
    PinChangeRequest: { current_pin: string; new_pin: string };

    Tone: 'ok' | 'warn' | 'bad' | 'accent';
    Badge: { value: string; tone: components['schemas']['Tone'] };
    NavItem: {
      /** Screen key; `null` for a pure group. */
      key: string | null;
      name: string;
      en?: string | null;
      /** Phosphor web class, e.g. `"ph ph-gauge"`. */
      icon: string;
      badge?: components['schemas']['Badge'] | null;
      children?: components['schemas']['NavItem'][];
      /** v0.1.42: có trong cây, có route, nhưng không hiện trên thanh bên (Hồ sơ sống, Plugin, Đánh giá/Chăm sóc khi chưa có nhân viên). */
      hidden?: boolean;
    };
    NavDomain: {
      domain: 'business' | 'tech' | string;
      label: string;
      crumb: string;
      icon: string;
      tone: components['schemas']['Tone'];
      /** v0.1.42: domain thu gọn mặc định trên thanh bên (Nâng cao). */
      collapsed?: boolean;
      count: number;
      groups: components['schemas']['NavItem'][];
    };
    HeaderStatus: {
      channels_live: number;
      /** v0.1.43: kênh đã từng đăng nhập thành công (kể cả khi phiên đã hết hạn) — tách "chưa nối" với "mất phiên". */
      channels_connected?: number;
      groups_listening: number;
      autonomy_level: number;
      /** 0–1 fraction or 0–100 percent; `null` while there is no data yet. */
      data_confidence: number | null;
    };
    Health: { status: 'ok' | string };
    Ready: { db: string; redis: string; objects: string; bridge: string };

    SetupStepStatus: 'todo' | 'doing' | 'done' | 'skipped';
    SetupStep: {
      n: number;
      key: string;
      title: string;
      required: boolean;
      status: components['schemas']['SetupStepStatus'];
      /** Phase 2: steps 4–7 and 12 report `available: true`; later steps may be absent/false. */
      available?: boolean;
    };
    SetupState: {
      finished: boolean;
      current_step: number;
      steps: components['schemas']['SetupStep'][];
    };
    SetupStep1: { token: string; language: 'vi' | 'en'; mode: 'empty' | 'sample' };
    SetupStep2: {
      token: string;
      display_name: string;
      email: string;
      password: string;
      pin: string;
      pin_confirm: string;
    };
    SetupStep3: {
      org_name: string;
      timezone: string;
      currency: string;
      self_name: string;
      bot_calls_me: string;
    };

    AuditItem: {
      id: string;
      at: string;
      actor_type: string;
      actor_id: string | null;
      actor_label: string | null;
      action: string;
      target_type: string | null;
      target_id: string | null;
      target_label: string | null;
      autonomy_level: number | null;
      result: string;
      detail: unknown;
    };
    AuditPage: { items: components['schemas']['AuditItem'][]; next_cursor: string | null };
    AuditVerify: { ok: boolean; checked: number; broken_at: string | null };
    Plugin: {
      package: string;
      name?: string;
      version?: string;
      layer?: string;
      origin?: string;
      enabled: boolean;
      [k: string]: unknown;
    };
  };
}

type S = components['schemas'];
type Json<T> = { content: { 'application/json': T } };
type Op<Req, Res> = {
  requestBody: Req extends void ? never : Json<Req>;
  responses: { 200: Json<Res> };
};

/** Path map in openapi-typescript shape (prefix `/api/v1` is implicit). */
export interface paths {
  '/auth/login': { post: Op<S['LoginRequest'], S['Me']> };
  '/auth/logout': { post: Op<void, void> };
  '/auth/me': { get: Op<void, S['Me']> };
  '/auth/pin/verify': { post: Op<S['PinVerifyRequest'], S['PinVerifyResponse']> };
  '/auth/pin': { put: Op<S['PinChangeRequest'], void> };
  '/navigation': { get: Op<void, S['NavDomain'][]> };
  '/header': { get: Op<void, S['HeaderStatus']> };
  '/health': { get: Op<void, S['Health']> };
  '/ready': { get: Op<void, S['Ready']> };
  '/setup/state': { get: Op<void, S['SetupState']> };
  '/setup/steps/1': { put: Op<S['SetupStep1'], S['SetupState']> };
  '/setup/steps/2': { put: Op<S['SetupStep2'], S['SetupState']> };
  '/setup/steps/3': { put: Op<S['SetupStep3'], S['SetupState']> };
  '/setup/steps/{n}/skip': { post: Op<void, S['SetupState']> };
  '/audit': { get: Op<void, S['AuditPage']> };
  '/audit/verify': { get: Op<void, S['AuditVerify']> };
  '/plugins': { get: Op<void, S['Plugin'][]> };
  '/plugins/{package}/toggle': { patch: Op<{ enabled: boolean }, S['Plugin']> };
  '/plugins/{package}': { delete: Op<void, void> };
}

// ── Aliases the rest of the code uses ─────────────────────────────────────
export type Problem = S['Problem'];
export type Me = S['Me'];
export type Tone = S['Tone'];
export type Badge = S['Badge'];
export type NavItem = S['NavItem'];
export type NavDomain = S['NavDomain'];
export type HeaderStatus = S['HeaderStatus'];
export type Health = S['Health'];
export type Ready = S['Ready'];
export type SetupState = S['SetupState'];
export type SetupStep = S['SetupStep'];
export type SetupStepStatus = S['SetupStepStatus'];
export type SetupStep1Body = S['SetupStep1'];
export type SetupStep2Body = S['SetupStep2'];
export type SetupStep3Body = S['SetupStep3'];
export type AuditItem = S['AuditItem'];
export type AuditPage = S['AuditPage'];
export type AuditVerify = S['AuditVerify'];
export type Plugin = S['Plugin'];
