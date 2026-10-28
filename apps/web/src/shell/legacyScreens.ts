import type { ComponentType } from 'react';
import { CleanScreen } from '../screens/data/CleanScreen';
import { IdentityScreen } from '../screens/data/IdentityScreen';
import { ListenLogScreen } from '../screens/data/ListenLogScreen';
import { RawScreen } from '../screens/data/RawScreen';
import { RulesScreen } from '../screens/data/RulesScreen';
import { SCREENS as QUEUE } from '../screens/queue';
import { SCREENS as RELATIONS } from '../screens/relations';
import { SCREENS as GRAPH } from '../screens/graph';
import { SCREENS as MARKET } from '../screens/market';
import { SCREENS as PEOPLE } from '../screens/people';
import { SCREENS as AGENTS } from '../screens/agents';
import { SCREENS as API } from '../screens/api';
import { SCREENS as MCP } from '../screens/mcp';
import { SCREENS as PLUGINS } from '../screens/plugins';

/**
 * v0.1.78 (G0): màn Console cũ theo khoá màn — nay chỉ được NHÚNG thành tab của trang mới (placeholder tới khi G1–G5 thay).
 * Chỉ đọc; không đăng ký màn mới ở đây.
 */
export const LEGACY_SCREENS: Record<string, ComponentType> = {
  raw: RawScreen,
  listenlog: ListenLogScreen,
  rules: RulesScreen,
  clean: CleanScreen,
  identity: IdentityScreen,
  ...QUEUE,
  ...RELATIONS,
  ...GRAPH,
  ...MARKET,
  ...PEOPLE,
  ...AGENTS,
  ...API,
  ...MCP,
  ...PLUGINS,
};
