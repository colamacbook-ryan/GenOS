import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  DirBotBody,
  DirBulkBotBody,
  DirGroupQuery,
  DirPeopleQuery,
  DocumentCreateBody,
  DocumentPatchBody,
  DocumentQuery,
  NbEntryBody,
  NbEntryPatchBody,
  NotebookSubjectType,
  ProfilePatchBody,
} from '@gen-harness/contracts';
import { api } from '../../lib/api';

/** Khoá truy vấn của cụm Quan hệ & Đối tượng. */
export const qkRel = {
  channels: ['relations', 'channels'] as const,
  groups: (q: DirGroupQuery) => ['relations', 'groups', JSON.stringify(q)] as const,
  people: (q: DirPeopleQuery) => ['relations', 'people', JSON.stringify(q)] as const,
  profile: (id: string) => ['relations', 'profile', id] as const,
  nbSubjects: (type: NotebookSubjectType) => ['relations', 'nb-subjects', type] as const,
  nb: (type: NotebookSubjectType, id: string) => ['relations', 'nb', type, id] as const,
  nbHistory: (type: NotebookSubjectType, id: string) => ['relations', 'nb-history', type, id] as const,
  nbDropped: (type: NotebookSubjectType, id: string) => ['relations', 'nb-dropped', type, id] as const,
  documents: (q: DocumentQuery) => ['relations', 'documents', JSON.stringify(q)] as const,
  document: (id: string) => ['relations', 'document', id] as const,
};

// ── Nhóm & Con người ──
export const useDirChannels = () =>
  useQuery({ queryKey: qkRel.channels, queryFn: ({ signal }) => api.relations.directory.channels(signal) });

export const useDirGroups = (q: DirGroupQuery) =>
  useQuery({ queryKey: qkRel.groups(q), queryFn: ({ signal }) => api.relations.directory.groups.list(q, signal) });

export const useDirPeople = (q: DirPeopleQuery, enabled = true) =>
  useQuery({ queryKey: qkRel.people(q), queryFn: ({ signal }) => api.relations.directory.people.list(q, signal), enabled });

/** Cỡ một trang của Khách & Nhóm — "Xem thêm" lấy trang kế bằng con trỏ `(created_at, id)` của máy chủ. */
export const DIR_PAGE_SIZE = 50;

/** Nhóm của MỘT kênh (hoặc mọi kênh khi `channel_id` rỗng), phân trang theo con trỏ — không tự cắt ở 50 dòng đầu. */
export const useDirGroupsPaged = (q: Omit<DirGroupQuery, 'cursor' | 'limit'>) =>
  useInfiniteQuery({
    queryKey: [...qkRel.groups(q), 'paged'] as const,
    queryFn: ({ signal, pageParam }) => api.relations.directory.groups.list({ ...q, limit: DIR_PAGE_SIZE, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });

/** Con người theo bộ lọc + tìm tên của máy chủ, phân trang theo con trỏ. */
export const useDirPeoplePaged = (q: Omit<DirPeopleQuery, 'cursor' | 'limit'>) =>
  useInfiniteQuery({
    queryKey: [...qkRel.people(q), 'paged'] as const,
    queryFn: ({ signal, pageParam }) => api.relations.directory.people.list({ ...q, limit: DIR_PAGE_SIZE, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });

export const useSetGroupBot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DirBotBody }) => api.relations.directory.groups.setBot(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['relations', 'groups'] }),
  });
};
export const useSetPersonBot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DirBotBody }) => api.relations.directory.people.setBot(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['relations', 'people'] }),
  });
};

/** Gán BOT / tự trị cho MỌI người khớp bộ lọc — một lần gọi, máy chủ làm trọn (không lặp từng người ở trình duyệt). */
export const useBulkSetPersonBot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DirBulkBotBody) => api.relations.directory.people.bulkBot(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['relations', 'people'] }),
  });
};

// ── Hồ sơ sống ──
export const useProfile = (id: string | null) =>
  useQuery({
    queryKey: qkRel.profile(id ?? ''),
    queryFn: ({ signal }) => api.relations.profile.get(id as string, signal),
    enabled: !!id,
  });

export const useUpdateProfile = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ProfilePatchBody }) => api.relations.profile.update(id, body),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qkRel.profile(vars.id) });
      void qc.invalidateQueries({ queryKey: ['relations', 'people'] });
    },
  });
};

// ── Sổ tay nhận thức ──
export const useNbSubjects = (type: NotebookSubjectType) =>
  useQuery({ queryKey: qkRel.nbSubjects(type), queryFn: ({ signal }) => api.relations.notebook.subjects({ type }, signal) });

export const useNotebook = (type: NotebookSubjectType, id: string | null) =>
  useQuery({
    queryKey: qkRel.nb(type, id ?? ''),
    queryFn: ({ signal }) => api.relations.notebook.get(type, id as string, signal),
    enabled: !!id,
  });

export const useNbHistory = (type: NotebookSubjectType, id: string | null) =>
  useQuery({
    queryKey: qkRel.nbHistory(type, id ?? ''),
    queryFn: ({ signal }) => api.relations.notebook.history(type, id as string, signal),
    enabled: !!id,
  });

export const useNbDropped = (type: NotebookSubjectType, id: string | null) =>
  useQuery({
    queryKey: qkRel.nbDropped(type, id ?? ''),
    queryFn: ({ signal }) => api.relations.notebook.dropped(type, id as string, {}, signal),
    enabled: !!id,
  });

function invalidateNb(qc: ReturnType<typeof useQueryClient>, type: NotebookSubjectType, id: string) {
  void qc.invalidateQueries({ queryKey: qkRel.nb(type, id) });
  void qc.invalidateQueries({ queryKey: qkRel.nbSubjects(type) });
  void qc.invalidateQueries({ queryKey: qkRel.nbHistory(type, id) });
  void qc.invalidateQueries({ queryKey: qkRel.nbDropped(type, id) });
}

export const useAddNbEntry = (type: NotebookSubjectType, id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NbEntryBody) => api.relations.notebook.addEntry(type, id, body),
    onSuccess: () => invalidateNb(qc, type, id),
  });
};
export const useUpdateNbEntry = (type: NotebookSubjectType, id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ eid, body }: { eid: string; body: NbEntryPatchBody }) => api.relations.notebook.updateEntry(type, id, eid, body),
    onSuccess: () => invalidateNb(qc, type, id),
  });
};
export const useDeleteNbEntry = (type: NotebookSubjectType, id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (eid: string) => api.relations.notebook.deleteEntry(type, id, eid),
    onSuccess: () => invalidateNb(qc, type, id),
  });
};
export const useCompactNb = (type: NotebookSubjectType, id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.relations.notebook.compact(type, id),
    onSuccess: () => invalidateNb(qc, type, id),
  });
};
export const useResetNb = (type: NotebookSubjectType, id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.relations.notebook.reset(type, id),
    onSuccess: () => invalidateNb(qc, type, id),
  });
};

// ── Tài liệu ──
export const useDocuments = (q: DocumentQuery) =>
  useQuery({ queryKey: qkRel.documents(q), queryFn: ({ signal }) => api.relations.documents.list(q, signal) });

export const useDocument = (id: string | null) =>
  useQuery({
    queryKey: qkRel.document(id ?? ''),
    queryFn: ({ signal }) => api.relations.documents.get(id as string, signal),
    enabled: !!id,
  });

function invalidateDocuments(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ['relations', 'documents'] });
}

export const useCreateDocument = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DocumentCreateBody) => api.relations.documents.create(body),
    onSuccess: () => invalidateDocuments(qc),
  });
};
export const useUpdateDocument = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DocumentPatchBody }) => api.relations.documents.update(id, body),
    onSuccess: (_d, vars) => {
      invalidateDocuments(qc);
      void qc.invalidateQueries({ queryKey: qkRel.document(vars.id) });
    },
  });
};
export const useDeleteDocument = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.relations.documents.remove(id),
    onSuccess: () => invalidateDocuments(qc),
  });
};
