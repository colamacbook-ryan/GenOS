/**
 * Mock API giai đoạn 4 · Plugin & Tiện ích (PLAN 4.4, ARCHITECTURE §6.2–§6.4, `apps/api/gh/plugins_api/routes.py`
 * + `apps/api/gh/chassis/plugins.py`): danh sách, bật/tắt nóng (khoá `can_disable`), gỡ (khoá `removable`),
 * reset breaker, nhật ký LIVE, nạp từ tệp (chữ ký + PIN).
 *
 * Thay hẳn stub `/plugins` cũ trong `mock-api.ts` (2 dòng tĩnh, không toggle thật) — `mock-api.ts` KHÔNG còn
 * chặn `/plugins*` trước khi tới đây (xem thay đổi ở đó).
 */
import { randomUUID } from 'node:crypto';
import type { PluginBreakerState, PluginHealth, PluginItem, PluginLogEntry, PluginOrigin } from '@gen-harness/contracts';
import type { P2Ctx } from './mock-phase2';

export interface P4PluginsOptions {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
}

interface MockPlugin extends PluginItem {
  dependencies: string[];
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

function seedPlugins(): MockPlugin[] {
  const core = (pkg: string, name: string, loadOrder: number, deps: string[], canDisable = true): MockPlugin => ({
    package: pkg, name, layer: loadOrder <= 5 ? 'chassis' : loadOrder === 6 ? 'intelligence' : 'ui', origin: 'core',
    version: '2.2.0', description: null, enabled: true, load_order: loadOrder, removable: false, can_disable: canDisable,
    sandbox: { mode: 'inprocess', memory_mb: 128, timeout_s: 30, net: 'internal' }, permissions: [], signature_ok: true,
    permissions_status: 'active', installed_at: ago(60 * 24 * 400), dependencies: deps, health: 'healthy',
    breaker: { state: 'closed', total_errors: 0, last_error: null },
  });
  return [
    core('@gen/chassis-kernel', 'Kernel & Plugin Manager', 1, [], false),
    core('@gen/chassis-bus', 'Event Bus', 2, ['@gen/chassis-kernel']),
    core('@gen/chassis-store', 'Store · SSOT', 3, ['@gen/chassis-bus']),
    core('@gen/chassis-policy', 'Policy Engine', 4, ['@gen/chassis-store']),
    core('@gen/chassis-auth', 'Định danh & Mã PIN', 5, ['@gen/chassis-store']),
    core('@gen/intel-core', 'Core Agent · sàng lọc & chấm điểm', 6, ['@gen/chassis-store', '@gen/chassis-policy']),
    core('@gen/ui-console', 'Web Console', 7, ['@gen/chassis-bus']),
    {
      package: '@gen/channel-whatsapp', name: 'Kênh WhatsApp', layer: 'channel', origin: 'marketplace', version: '1.3.0',
      description: 'Cầu nối WhatsApp song song với Zalo.', enabled: true, load_order: 8, removable: true, can_disable: true,
      sandbox: { mode: 'subprocess', memory_mb: 256, timeout_s: 20, net: 'internal' }, permissions: ['read:raw', 'write:raw'],
      signature_ok: true, permissions_status: 'active', installed_at: ago(60 * 24 * 60), dependencies: ['@gen/chassis-bus', '@gen/chassis-policy'],
      health: 'degraded', breaker: { state: 'closed', total_errors: 3, last_error: 'mất phiên đăng nhập' },
    },
    {
      package: '@gen/provider-deepseek', name: 'Provider DeepSeek', layer: 'provider', origin: 'marketplace', version: '1.0.4',
      description: 'Kết nối model DeepSeek.', enabled: true, load_order: 9, removable: true, can_disable: true,
      sandbox: { mode: 'subprocess', memory_mb: 128, timeout_s: 30, net: 'public' }, permissions: ['call:provider'],
      signature_ok: true, permissions_status: 'active', installed_at: ago(60 * 24 * 90), dependencies: ['@gen/chassis-bus'],
      health: 'degraded', breaker: { state: 'half_open', total_errors: 7, last_error: 'HTTP 429 — hết hạn mức' },
    },
    {
      package: '@gen/tool-media', name: 'Xử lý media', layer: 'extension', origin: 'marketplace', version: '0.6.1',
      description: 'Nhận diện và nén ảnh/video đính kèm.', enabled: false, load_order: 10, removable: true, can_disable: true,
      sandbox: { mode: 'subprocess', memory_mb: 512, timeout_s: 30, net: 'none' }, permissions: ['read:raw'],
      signature_ok: true, permissions_status: 'active', installed_at: ago(60 * 24 * 40), dependencies: ['@gen/chassis-bus'],
      health: 'disabled', breaker: { state: 'closed', total_errors: 0, last_error: null },
    },
    {
      package: '@gen/action-office', name: 'Soạn văn bản office', layer: 'action', origin: 'marketplace', version: '3.6.0',
      description: 'Soạn báo giá/hợp đồng dạng .docx từ bản nháp đã duyệt.', enabled: true, load_order: 11, removable: true,
      can_disable: true, sandbox: { mode: 'subprocess', memory_mb: 384, timeout_s: 30, net: 'none' }, permissions: ['read:clean', 'write:documents'],
      signature_ok: true, permissions_status: 'active', installed_at: ago(60 * 24 * 20), dependencies: ['@gen/chassis-bus'],
      health: 'healthy', breaker: { state: 'closed', total_errors: 0, last_error: null },
    },
  ];
}

function seedLogs(plugins: MockPlugin[]): Map<string, PluginLogEntry[]> {
  const m = new Map<string, PluginLogEntry[]>();
  for (const p of plugins) {
    const lines: PluginLogEntry[] = [{ id: randomUUID(), at: p.installed_at, level: 'INFO', message: `Nạp ${p.name} · thứ tự ${p.load_order ?? '—'}`, ctx: null }];
    if (p.breaker.last_error) lines.unshift({ id: randomUUID(), at: ago(30), level: p.breaker.state === 'open' ? 'ERROR' : 'WARN', message: `${p.name}: ${p.breaker.last_error}`, ctx: null });
    if (!p.enabled) lines.unshift({ id: randomUUID(), at: ago(200), level: 'INFO', message: `Sếp tắt ${p.name}`, ctx: null });
    m.set(p.package, lines);
  }
  return m;
}

export function createMock(opts: P4PluginsOptions) {
  let plugins: MockPlugin[] = opts.fresh ? [] : seedPlugins();
  const logs = opts.fresh ? new Map<string, PluginLogEntry[]>() : seedLogs(plugins);

  const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';
  const pin = (ctx: P2Ctx, operation: string) => {
    if (!ctx.needPin()) return true;
    ctx.problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation } });
    return false;
  };
  function pushLog(pkg: string, level: string, message: string) {
    const entry: PluginLogEntry = { id: randomUUID(), at: new Date().toISOString(), level, message, ctx: null };
    logs.set(pkg, [entry, ...(logs.get(pkg) ?? [])]);
    opts.emit('plugin.log', { ...entry, package: pkg });
  }
  function toPublic(p: MockPlugin): PluginItem {
    return p;
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    if (p !== '/plugins' && !p.startsWith('/plugins/')) return false;
    const seg = p.split('/').filter(Boolean); // ['plugins', ...]

    if (seg.length === 1 && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const sorted = [...plugins].sort((a, b) => (a.origin === 'core' ? 0 : 1) - (b.origin === 'core' ? 0 : 1) || (a.load_order ?? 999) - (b.load_order ?? 999) || a.package.localeCompare(b.package));
      return reply(200, sorted.map(toPublic));
    }

    if (seg[1] === 'local' && seg.length === 2 && m === 'POST') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'plugin.install')) return true;
      const b = body as { manifest?: Record<string, unknown>; code_sha256?: string; signature?: string };
      const manifest = b.manifest ?? {};
      const pkg = String(manifest.package ?? '').trim();
      const name = String(manifest.name ?? '').trim();
      const version = String(manifest.version ?? '').trim();
      const layer = String(manifest.layer ?? '').trim();
      const errors: Record<string, string> = {};
      if (!pkg) errors['manifest.package'] = 'Không được để trống';
      if (!name) errors['manifest.name'] = 'Không được để trống';
      if (!version) errors['manifest.version'] = 'Không được để trống';
      if (!['chassis', 'channel', 'intelligence', 'provider', 'action', 'ui', 'extension'].includes(layer)) errors['manifest.layer'] = 'Layer không hợp lệ';
      if (!/^[0-9a-f]{64}$/i.test(String(b.code_sha256 ?? ''))) errors.code_sha256 = 'Phải là chuỗi hex sha256 (64 ký tự)';
      if (Object.keys(errors).length) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });
      if (plugins.some((x) => x.package === pkg)) return problem(409, 'PLUGIN_EXISTS', `Đã có plugin ${pkg}`);
      const sig = String(b.signature ?? '');
      if (!sig || sig.toUpperCase().startsWith('BAD')) {
        return problem(409, 'SIGNATURE_INVALID', 'Chữ ký không hợp lệ — không cài plugin chưa xác thực được nguồn');
      }
      const permissions = Array.isArray(manifest.permissions) ? (manifest.permissions as string[]) : [];
      const item: MockPlugin = {
        package: pkg, name, layer, origin: 'local_file' as PluginOrigin, version, description: (manifest.description as string) || null,
        enabled: false, load_order: typeof manifest.load_order === 'number' ? manifest.load_order : null, removable: true, can_disable: true,
        sandbox: (manifest.sandbox as Record<string, unknown>) ?? { mode: 'subprocess', memory_mb: 512, timeout_s: 30, net: 'none' },
        permissions, signature_ok: true, permissions_status: 'pending', installed_at: new Date().toISOString(), dependencies: [],
        health: 'disabled' as PluginHealth, breaker: { state: 'closed' as PluginBreakerState, total_errors: 0, last_error: null },
      };
      plugins = [...plugins, item];
      pushLog(pkg, 'INFO', `Nạp ${name} từ tệp · chờ Sếp bật`);
      return reply(201, {
        id: randomUUID(), package: pkg, name, version, origin: 'local_file', is_enabled: false,
        permissions_status: 'pending', signature_ok: true, permissions, installed_at: item.installed_at,
      });
    }

    const pkgSeg = decodeURIComponent(seg[1] ?? '');
    const plugin = plugins.find((x) => x.package === pkgSeg);

    if (seg[2] === 'toggle' && seg.length === 3 && m === 'PATCH') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'plugin.toggle')) return true;
      if (!plugin) return problem(404, 'NOT_FOUND', 'Plugin');
      const enabled = (body as { enabled?: boolean }).enabled === true;
      if (enabled === plugin.enabled) return reply(200, toPublic(plugin));
      if (!enabled && !plugin.can_disable) return problem(409, 'PLUGIN_LOCKED', `${plugin.name} là plugin nền, không tắt được`);
      if (!enabled) {
        const dependents = plugins.filter((o) => o.enabled && o.dependencies.includes(plugin.package));
        if (dependents.length) return problem(409, 'DEPENDED_ON', `${dependents[0].name} đang cần plugin này`);
      }
      if (enabled) {
        const off = plugin.dependencies.map((d) => plugins.find((x) => x.package === d)).filter((d): d is MockPlugin => !!d && !d.enabled);
        if (off.length) return problem(409, 'DEPENDENCY_DISABLED', `Cần bật ${off[0].name} trước`);
      }
      plugin.enabled = enabled;
      plugin.health = enabled ? 'healthy' : 'disabled';
      if (enabled) plugin.breaker = { state: 'closed', total_errors: plugin.breaker.total_errors, last_error: plugin.breaker.last_error };
      pushLog(plugin.package, 'INFO', enabled ? 'đã bật' : 'đã tắt');
      return reply(200, toPublic(plugin));
    }

    if (seg[2] === 'breaker' && seg[3] === 'reset' && seg.length === 4 && m === 'POST') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!plugin) return problem(404, 'NOT_FOUND', 'Plugin');
      plugin.breaker = { state: 'closed', total_errors: plugin.breaker.total_errors, last_error: plugin.breaker.last_error };
      if (plugin.enabled) plugin.health = 'healthy';
      pushLog(plugin.package, 'INFO', 'đã đóng ngắt mạch (reset tay)');
      return reply(200, toPublic(plugin));
    }

    if (seg[2] === 'logs' && seg.length === 3 && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!plugin) return problem(404, 'NOT_FOUND', 'Plugin');
      const limit = Number(url.searchParams.get('limit') ?? 50);
      const rows = logs.get(plugin.package) ?? [];
      return reply(200, { items: rows.slice(0, limit), next_cursor: null });
    }

    if (seg.length === 2 && m === 'DELETE') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'plugin.uninstall')) return true;
      if (!plugin) return problem(404, 'NOT_FOUND', 'Plugin');
      if (plugin.origin === 'core' || !plugin.removable) return problem(409, 'PLUGIN_LOCKED', `${plugin.name} là plugin nền, không gỡ được`);
      plugins = plugins.filter((x) => x.package !== plugin.package);
      logs.delete(plugin.package);
      return reply(204);
    }

    return false;
  }

  return {
    handle,
    hooks: {
      list: () => plugins,
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
