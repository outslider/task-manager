/* Shared application state and cached lookups. */
import { api } from './api.js';

export const store = {
  user: null,
  meta: null,
  users: [],
  usersById: new Map(),
  groups: [],
  projects: [],
  memberCache: new Map(),
  unread: 0,
  ui: { accent_default: '#3b6ef5', app_name: 'タスク管理' },
  listeners: new Set(),

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); },
  emit() { this.listeners.forEach((fn) => fn(this)); },

  async loadSession() {
    const data = await api.me();
    this.user = data.user;
    this.unread = data.unread || 0;
    if (data.ui) this.ui = data.ui;
    return this.user;
  },

  /** The accent the current user should see: personal choice, else the org default. */
  accent() {
    return this.user?.ui_accent || this.ui.accent_default || '#3b6ef5';
  },

  async loadBase() {
    const [meta, users, projects] = await Promise.all([
      api.meta(), api.users(), api.projects({ include_archived: 1 }),
    ]);
    this.meta = meta;
    applyTaxonomy(meta);
    this.setUsers(users.users);
    this.projects = projects.projects;
    this.emit();
  },

  setUsers(users) {
    this.users = users;
    this.usersById = new Map(users.map((u) => [u.id, u]));
  },

  async refreshProjects() {
    const data = await api.projects({ include_archived: 1 });
    this.projects = data.projects;
    this.emit();
    return this.projects;
  },

  async refreshUnread() {
    const data = await api.me();
    this.unread = data.unread || 0;
    this.emit();
  },

  project(id) {
    return this.projects.find((p) => p.id === Number(id)) || null;
  },

  /** 担当者に指定できる人。プロジェクトのメンバー（グループ経由も含む）。 */
  async members(projectId) {
    if (!projectId) return this.users;
    const key = Number(projectId);
    if (!this.memberCache.has(key)) {
      this.memberCache.set(key, api.project(key)
        .then((data) => data.member_users || this.users)
        .catch(() => this.users));
    }
    return this.memberCache.get(key);
  },

  /** メンバーを編集したあとに呼ぶ。 */
  forgetMembers(projectId) {
    if (projectId) this.memberCache.delete(Number(projectId));
    else this.memberCache.clear();
  },

  userName(id) {
    return this.usersById.get(Number(id))?.name || '未割当';
  },

  isAdmin() {
    return this.user?.role === 'admin';
  },

  canEdit(project) {
    const role = typeof project === 'object' ? project?.my_role : this.project(project)?.my_role;
    return role === 'owner' || role === 'editor';
  },

  canComment(project) {
    const role = typeof project === 'object' ? project?.my_role : this.project(project)?.my_role;
    return role === 'owner' || role === 'editor' || role === 'commenter';
  },
};

/* 状態とカテゴリは管理画面から変えられる。/api/meta で受け取った内容で
 * 中身を差し替えるので、各画面は今までどおり参照するだけでよい。 */
export const STATUS_LABEL = {
  todo: '未着手', doing: '進行中', review: 'レビュー中', done: '完了', blocked: 'ブロック中',
};
export const STATUS_COLOR = {
  todo: '#98a2b3', doing: '#3b6ef5', review: '#9061f9', done: '#17a673', blocked: '#e14c4c',
};

/** サーバーの定義で、上の一覧を丸ごと置き換える。 */
export function applyTaxonomy(meta) {
  if (Array.isArray(meta?.statuses) && meta.statuses.length) {
    for (const key of Object.keys(STATUS_LABEL)) {
      delete STATUS_LABEL[key];
      delete STATUS_COLOR[key];
    }
    for (const row of meta.statuses) {
      STATUS_LABEL[row.value] = row.label;
      STATUS_COLOR[row.value] = row.color;
    }
  }
  if (Array.isArray(meta?.categories)) {
    CATEGORIES.length = 0;
    CATEGORIES.push(...meta.categories);
  }
}
/** 重要度。緊急度は期限から自動的に決まるので、この軸は純粋な重要度だけ。 */
export const IMPORTANCE_LABEL = { 0: '低', 1: '中', 2: '高', 3: '最重要' };

export const CATEGORIES = [
  { value: 'research', label: '調査・リサーチ', color: '#6366f1', icon: '🔍' },
  { value: 'design', label: '設計・企画', color: '#8b5cf6', icon: '✏️' },
  { value: 'build', label: '実装・構築', color: '#3b6ef5', icon: '🔧' },
  { value: 'docs', label: 'ドキュメント作成', color: '#0ea5e9', icon: '📄' },
  { value: 'meeting', label: '会議・打ち合わせ', color: '#14b8a6', icon: '👥' },
  { value: 'admin', label: '事務・申請系', color: '#a1a1aa', icon: '📋' },
  { value: 'incident', label: 'トラブル対応・障害対応', color: '#ef4444', icon: '🚨' },
];

const UNCATEGORIZED = { value: '', label: '未分類', color: '#98a2b3', icon: '·' };

/* ---- 課題管理表 ---- */

export const ISSUE_STATUS_LABEL = {
  open: '未対応', doing: '対応中', pending: '保留', resolved: '解決済', closed: 'クローズ',
};
export const ISSUE_OPEN_STATUSES = ['open', 'doing', 'pending'];
export const SEVERITY_LABEL = { 0: '低', 1: '中', 2: '高', 3: '重大' };

export const ISSUE_CATEGORIES = [
  { value: 'spec', label: '仕様・要件', color: '#6366f1' },
  { value: 'tech', label: '技術・実装', color: '#3b6ef5' },
  { value: 'schedule', label: 'スケジュール', color: '#e8912b' },
  { value: 'resource', label: '体制・リソース', color: '#14b8a6' },
  { value: 'cost', label: 'コスト・予算', color: '#8b5cf6' },
  { value: 'quality', label: '品質・不具合', color: '#ef4444' },
  { value: 'external', label: '外部・他部門', color: '#0ea5e9' },
  { value: 'other', label: 'その他', color: '#a1a1aa' },
];

export function issueCategory(value) {
  return ISSUE_CATEGORIES.find((c) => c.value === value)
    || { value: 'other', label: 'その他', color: '#a1a1aa' };
}

/** Category descriptor, falling back to 未分類 for empty or unknown values. */
export function category(value) {
  return CATEGORIES.find((c) => c.value === value) || UNCATEGORIZED;
}
