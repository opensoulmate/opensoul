'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, BookOpen, Plus, Trash2, RefreshCw, X, Save, GraduationCap,
  Loader2, AlertCircle, CheckCircle, ChevronRight, ChevronDown,
  FileText, HelpCircle, Award, Clock, Edit3, Sparkles, BarChart3,
  CheckSquare, Square, ArrowLeft
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface QuizQuestion {
  question: string;
  options: string[];
  correctIndex: number;
  explanation: string;
}

interface Chapter {
  id: string;
  title: string;
  content: string;
  order: number;
  completed: boolean;
  completedAt: string | null;
  quiz: QuizQuestion[];
}

interface Course {
  id: string;
  title: string;
  description: string;
  tags: string[];
  topics: string[];
  domain: string;
  knowledge_ids: string[];
  totalChapters: number;
  completedChapters: number;
  status: string;
  generated_by: string;
  createdAt: string;
  updatedAt: string;
  chapters?: Chapter[];
}

interface LearnStats {
  total_courses: number;
  total_chapters: number;
  completed_chapters: number;
  total_quizzes: number;
}

export default function LearnPage() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [stats, setStats] = useState<LearnStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  // Detail view
  const [selectedCourse, setSelectedCourse] = useState<Course | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [expandedChapter, setExpandedChapter] = useState<string | null>(null);

  // Create course dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ title: '', description: '', tags: '', topics: '', domain: '' });
  const [creating, setCreating] = useState(false);

  // AI Generate dialog
  const [showGenerate, setShowGenerate] = useState(false);
  const [generateForm, setGenerateForm] = useState({ topic: '', num_chapters: '5', difficulty: 'intermediate' });
  const [generating, setGenerating] = useState(false);

  // Add chapter dialog
  const [showAddChapter, setShowAddChapter] = useState(false);
  const [chapterForm, setChapterForm] = useState({ title: '', content: '' });
  const [addingChapter, setAddingChapter] = useState(false);

  // Edit chapter
  const [editChapter, setEditChapter] = useState<Chapter | null>(null);
  const [showEditChapter, setShowEditChapter] = useState(false);
  const [editChapterForm, setEditChapterForm] = useState({ title: '', content: '', order: '' });

  // Quiz view
  const [quizChapter, setQuizChapter] = useState<Chapter | null>(null);
  const [showQuiz, setShowQuiz] = useState(false);
  const [quizAnswers, setQuizAnswers] = useState<Record<number, number>>({});
  const [quizSubmitted, setQuizSubmitted] = useState(false);

  const fetchCourses = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (statusFilter) params.set('status', statusFilter);
      const data = await apiFetch(`/api/learn/courses?${params}`);
      setCourses(Array.isArray(data.courses) ? data.courses : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/learn/stats');
      setStats(data);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => { fetchCourses(); fetchStats(); }, [fetchCourses, fetchStats]);

  const fetchCourseDetail = async (courseId: string) => {
    try {
      setDetailLoading(true);
      const data = await apiFetch(`/api/learn/courses/${courseId}`);
      setSelectedCourse(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDetailLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!createForm.title.trim()) { setError('课程标题必填'); return; }
    try {
      setCreating(true);
      await apiFetch('/api/learn/courses', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: createForm.title,
          description: createForm.description,
          tags: createForm.tags.split(',').map(t => t.trim()).filter(Boolean),
          topics: createForm.topics.split(',').map(t => t.trim()).filter(Boolean),
          domain: createForm.domain,
        }),
      });
      setShowCreate(false);
      setCreateForm({ title: '', description: '', tags: '', topics: '', domain: '' });
      fetchCourses();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleGenerate = async () => {
    if (!generateForm.topic.trim()) { setError('主题必填'); return; }
    try {
      setGenerating(true);
      const data = await apiFetch('/api/learn/courses/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: generateForm.topic,
          num_chapters: parseInt(generateForm.num_chapters) || 5,
          difficulty: generateForm.difficulty,
        }),
      });
      setShowGenerate(false);
      setGenerateForm({ topic: '', num_chapters: '5', difficulty: 'intermediate' });
      fetchCourses();
      fetchStats();
      if (data.id) fetchCourseDetail(data.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  };

  const handleDeleteCourse = async (courseId: string) => {
    if (!confirm('确定要删除该课程及其所有章节？')) return;
    try {
      await apiFetch(`/api/learn/courses/${courseId}`, { method: 'DELETE' });
      if (selectedCourse?.id === courseId) setSelectedCourse(null);
      fetchCourses();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpdateStatus = async (courseId: string, status: string) => {
    try {
      await apiFetch(`/api/learn/courses/${courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      fetchCourses();
      if (selectedCourse?.id === courseId) fetchCourseDetail(courseId);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleAddChapter = async () => {
    if (!selectedCourse || !chapterForm.title.trim()) return;
    try {
      setAddingChapter(true);
      await apiFetch(`/api/learn/courses/${selectedCourse.id}/chapters`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: chapterForm.title, content: chapterForm.content }),
      });
      setShowAddChapter(false);
      setChapterForm({ title: '', content: '' });
      fetchCourseDetail(selectedCourse.id);
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAddingChapter(false);
    }
  };

  const handleUpdateChapter = async () => {
    if (!selectedCourse || !editChapter) return;
    try {
      const body: any = {};
      if (editChapterForm.title) body.title = editChapterForm.title;
      if (editChapterForm.content) body.content = editChapterForm.content;
      if (editChapterForm.order) body.order = parseInt(editChapterForm.order);
      await apiFetch(`/api/learn/courses/${selectedCourse.id}/chapters/${editChapter.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setShowEditChapter(false);
      setEditChapter(null);
      fetchCourseDetail(selectedCourse.id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeleteChapter = async (chapterId: string) => {
    if (!selectedCourse) return;
    if (!confirm('确定要删除该章节？')) return;
    try {
      await apiFetch(`/api/learn/courses/${selectedCourse.id}/chapters/${chapterId}`, { method: 'DELETE' });
      fetchCourseDetail(selectedCourse.id);
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleMarkChapter = async (chapterId: string, completed: boolean) => {
    if (!selectedCourse) return;
    try {
      await apiFetch(`/api/learn/courses/${selectedCourse.id}/chapters/${chapterId}/mark`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ completed }),
      });
      fetchCourseDetail(selectedCourse.id);
      fetchCourses();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const openQuiz = (chapter: Chapter) => {
    setQuizChapter(chapter);
    setQuizAnswers({});
    setQuizSubmitted(false);
    setShowQuiz(true);
  };

  const getProgressPercent = (course: Course) => {
    if (course.totalChapters === 0) return 0;
    return Math.round((course.completedChapters / course.totalChapters) * 100);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-emerald-500/10 text-emerald-500';
      case 'in_progress': return 'bg-blue-500/10 text-blue-500';
      case 'archived': return 'bg-muted text-muted-foreground';
      default: return 'bg-amber-500/10 text-amber-500';
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'completed': return '已完成';
      case 'in_progress': return '进行中';
      case 'archived': return '已归档';
      default: return '草稿';
    }
  };

  const filtered = courses.filter((c) => {
    if (search) {
      const q = search.toLowerCase();
      return c.title.toLowerCase().includes(q) || c.description.toLowerCase().includes(q) || c.domain.toLowerCase().includes(q);
    }
    return true;
  });

  // ── Quiz View ──
  if (showQuiz && quizChapter) {
    return (
      <div className="space-y-4">
        <Button variant="bare" onClick={() => setShowQuiz(false)} className="flex items-center gap-1 text-xs lg:text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="w-3 h-3" />返回课程
        </Button>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 mb-4">
            <HelpCircle className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-medium">测验 — {quizChapter.title}</h2>
            <Badge variant="muted">{quizChapter.quiz.length} 题</Badge>
          </div>
          {quizChapter.quiz.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <HelpCircle className="w-10 h-10 mb-2 opacity-30 mx-auto" />
              <p className="text-xs">该章节暂无测验题</p>
            </div>
          ) : (
            <div className="space-y-4">
              {quizChapter.quiz.map((q, qi) => (
                <div key={qi} className="rounded border border-border p-3">
                  <p className="text-xs font-medium mb-2">{qi + 1}. {q.question}</p>
                  <div className="space-y-1.5">
                    {q.options.map((opt, oi) => {
                      const selected = quizAnswers[qi] === oi;
                      const isCorrect = q.correctIndex === oi;
                      const showResult = quizSubmitted;
                      return (
                        <Button
                          key={oi}
                          onClick={() => !quizSubmitted && setQuizAnswers(prev => ({ ...prev, [qi]: oi }))}
                          variant="chip-sm" className={`w-full text-left flex items-center gap-2 transition-colors  ${
                            showResult
                              ? isCorrect
                                ? 'bg-green-500/10 border border-emerald-500/30 text-green-700'
                                : selected
                                  ? 'bg-red-500/10 border border-red-500/30 text-red-600'
                                  : 'bg-muted/30 border border-transparent'
                              : selected
                                ? 'bg-primary/10 border border-primary/30'
                                : 'bg-muted/30 border border-transparent hover:bg-muted/50'
                          }`}
                        >
                          {showResult ? (
                            isCorrect ? <CheckCircle className="w-3 h-3 text-green-600 shrink-0" /> : selected ? <X className="w-3 h-3 text-red-500 shrink-0" /> : <Square className="w-3 h-3 text-muted-foreground shrink-0" />
                          ) : (
                            selected ? <CheckSquare className="w-3 h-3 text-primary shrink-0" /> : <Square className="w-3 h-3 text-muted-foreground shrink-0" />
                          )}
                          <span>{String.fromCharCode(65 + oi)}. {opt}</span>
                        </Button>
                      );
                    })}
                  </div>
                  {quizSubmitted && q.explanation && (
                    <div className="mt-2 p-2 rounded bg-blue-500/5 border border-blue-500/20 text-[11px] text-blue-700">
                      💡 {q.explanation}
                    </div>
                  )}
                </div>
              ))}
              <div className="flex items-center gap-3 pt-2">
                {!quizSubmitted ? (
                  <Button onClick={() => setQuizSubmitted(true)}
                    disabled={Object.keys(quizAnswers).length < quizChapter.quiz.length} variant="primary" className="px-4 py-1.5">
                    <Award className="w-3 h-3" />提交答案
                  </Button>
                ) : (
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-medium">
                      得分: {quizChapter.quiz.filter((q, i) => quizAnswers[i] === q.correctIndex).length}/{quizChapter.quiz.length}
                    </span>
                    <Button onClick={() => { setQuizAnswers({}); setQuizSubmitted(false); }} variant="secondary">
                      重新答题
                    </Button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Course Detail View ──
  if (selectedCourse) {
    const chapters = selectedCourse.chapters || [];
    return (
      <div className="space-y-4">
        <Button variant="bare" onClick={() => setSelectedCourse(null)} className="flex items-center gap-1 text-xs lg:text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="w-3 h-3" />返回课程列表
        </Button>

        {/* Course header */}
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-start justify-between">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <GraduationCap className="w-5 h-5 text-primary" />
                <h2 className="text-base font-medium">{selectedCourse.title}</h2>
                <Badge variant="bare" className={`${getStatusColor(selectedCourse.status)}`}>
                  {getStatusLabel(selectedCourse.status)}
                </Badge>
                {selectedCourse.generated_by === 'ai' && (
                  <Badge variant="accent">AI生成</Badge>
                )}
              </div>
              {selectedCourse.description && <p className="text-xs text-muted-foreground mt-1">{selectedCourse.description}</p>}
              <div className="flex items-center gap-3 mt-2 text-[10px] text-muted-foreground">
                <span>📚 {selectedCourse.totalChapters} 章节</span>
                <span>✅ {selectedCourse.completedChapters} 已完成</span>
                {selectedCourse.domain && <span>📁 {selectedCourse.domain}</span>}
              </div>
              {selectedCourse.tags.length > 0 && (
                <div className="flex items-center gap-1 mt-2">
                  {selectedCourse.tags.map(t => <Badge variant="subtle" key={t}>{t}</Badge>)}
                </div>
              )}
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <select
                className="px-2 py-1 text-[10px] bg-muted border border-border rounded-md"
                value={selectedCourse.status}
                onChange={(e) => handleUpdateStatus(selectedCourse.id, e.target.value)}
              >
                <option value="draft">草稿</option>
                <option value="in_progress">进行中</option>
                <option value="completed">已完成</option>
                <option value="archived">已归档</option>
              </select>
              <Button onClick={() => handleDeleteCourse(selectedCourse.id)} variant="icon-md" className="hover:bg-red-500/10" title="删除课程">
                <Trash2 className="w-3.5 h-3.5 text-red-500" />
              </Button>
            </div>
          </div>
          {/* Progress bar */}
          <div className="mt-3">
            <div className="flex items-center justify-between text-[10px] mb-1">
              <span className="text-muted-foreground">学习进度</span>
              <span className="font-medium">{getProgressPercent(selectedCourse)}%</span>
            </div>
            <div className="w-full bg-muted rounded-full h-2">
              <div className="h-2 rounded-full bg-primary transition-all" style={{ width: `${getProgressPercent(selectedCourse)}%` }} />
            </div>
          </div>
        </div>

        {/* Chapters toolbar */}
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-muted-foreground" />
          <span className="text-xs font-medium">章节列表</span>
          <Badge variant="muted">{chapters.length}</Badge>
          <Button onClick={() => setShowAddChapter(true)} variant="primary" className="ml-auto">
            <Plus className="w-3 h-3" />添加章节
          </Button>
        </div>

        {/* Chapters */}
        {detailLoading ? (
          <div className="text-center py-8 text-xs text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin mx-auto mb-1" />加载中...</div>
        ) : chapters.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <FileText className="w-10 h-10 mb-2 opacity-30 mx-auto" />
            <p className="text-xs">暂无章节，点击上方按钮添加</p>
          </div>
        ) : (
          <div className="space-y-2">
            {chapters.sort((a, b) => a.order - b.order).map((ch) => (
              <div key={ch.id} className={`rounded-lg border transition-colors ${ch.completed ? 'border-green-500/20 bg-green-500/5' : 'border-border bg-card'}`}>
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/30"
                  onClick={() => setExpandedChapter(expandedChapter === ch.id ? null : ch.id)}
                >
                  <Button variant="bare"
                    onClick={(e) => { e.stopPropagation(); handleMarkChapter(ch.id, !ch.completed); }}
                    className="shrink-0"
                  >
                    {ch.completed ? <CheckCircle className="w-4 h-4 text-green-600" /> : <Square className="w-4 h-4 text-muted-foreground" />}
                  </Button>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground font-mono">#{ch.order}</span>
                      <h3 className={`text-xs font-medium ${ch.completed ? 'line-through text-muted-foreground' : ''}`}>{ch.title}</h3>
                    </div>
                    {ch.completedAt && <span className="text-[10px] text-muted-foreground">完成于 {new Date(ch.completedAt).toLocaleDateString('zh-CN')}</span>}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {ch.quiz.length > 0 && (
                      <Button onClick={(e) => { e.stopPropagation(); openQuiz(ch); }} variant="chip-xs" className="flex items-center gap-1 bg-blue-500/10 text-blue-500 hover:bg-blue-500/20" title="测验">
                        <HelpCircle className="w-3 h-3" />{ch.quiz.length}题
                      </Button>
                    )}
                    <Button onClick={(e) => { e.stopPropagation(); setEditChapter(ch); setEditChapterForm({ title: ch.title, content: ch.content, order: String(ch.order) }); setShowEditChapter(true); }} variant="icon-sm" className="hover:bg-muted" title="编辑">
                      <Edit3 className="w-3 h-3 text-muted-foreground" />
                    </Button>
                    <Button onClick={(e) => { e.stopPropagation(); handleDeleteChapter(ch.id); }} variant="icon-sm" className="hover:bg-red-500/10" title="删除">
                      <Trash2 className="w-3 h-3 text-red-500" />
                    </Button>
                    {expandedChapter === ch.id ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />}
                  </div>
                </div>
                {expandedChapter === ch.id && (
                  <div className="px-4 pb-3 border-t border-border pt-2">
                    {ch.content ? (
                      <div className="text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed max-h-60 overflow-auto">{ch.content}</div>
                    ) : (
                      <p className="text-xs text-muted-foreground italic">暂无内容</p>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Add Chapter Dialog */}
        {showAddChapter && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowAddChapter(false)}>
            <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between p-4 border-b border-border">
                <h2 className="text-sm font-medium">添加章节</h2>
                <Button onClick={() => setShowAddChapter(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
              </div>
              <div className="p-4 space-y-3">
                <div>
                  <label className="text-xs text-muted-foreground">章节标题 *</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={chapterForm.title} onChange={(e) => setChapterForm(f => ({ ...f, title: e.target.value }))} placeholder="第一章 认识..." />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">内容 (Markdown)</label>
                  <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md h-40 resize-none font-mono" value={chapterForm.content} onChange={(e) => setChapterForm(f => ({ ...f, content: e.target.value }))} placeholder="章节内容..." />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button onClick={() => setShowAddChapter(false)} variant="secondary">取消</Button>
                  <Button onClick={handleAddChapter} disabled={!chapterForm.title.trim() || addingChapter} variant="primary">
                    {addingChapter ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}添加
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Edit Chapter Dialog */}
        {showEditChapter && editChapter && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowEditChapter(false)}>
            <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between p-4 border-b border-border">
                <h2 className="text-sm font-medium">编辑章节</h2>
                <Button onClick={() => setShowEditChapter(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
              </div>
              <div className="p-4 space-y-3">
                <div>
                  <label className="text-xs text-muted-foreground">标题</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={editChapterForm.title} onChange={(e) => setEditChapterForm(f => ({ ...f, title: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">内容</label>
                  <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md h-40 resize-none font-mono" value={editChapterForm.content} onChange={(e) => setEditChapterForm(f => ({ ...f, content: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">排序</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md w-24" type="number" value={editChapterForm.order} onChange={(e) => setEditChapterForm(f => ({ ...f, order: e.target.value }))} />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button onClick={() => setShowEditChapter(false)} variant="secondary">取消</Button>
                  <Button onClick={handleUpdateChapter} variant="primary">
                    <Save className="w-3 h-3" />保存
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── Course List View ──
  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_courses ?? courses.length}</div>
          <div className="text-xs text-muted-foreground">课程总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.total_chapters ?? 0}</div>
          <div className="text-xs text-muted-foreground">总章节</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{stats?.completed_chapters ?? 0}</div>
          <div className="text-xs text-muted-foreground">已完成</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{stats?.total_quizzes ?? 0}</div>
          <div className="text-xs text-muted-foreground">测验题</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索课程..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">全部状态</option>
          <option value="draft">草稿</option>
          <option value="in_progress">进行中</option>
          <option value="completed">已完成</option>
          <option value="archived">已归档</option>
        </select>
        <Button onClick={() => { fetchCourses(); fetchStats(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowGenerate(true)} variant="chip-sm" className="flex items-center gap-1.5 bg-violet-500/10 text-violet-500 border border-purple-500/20 hover:bg-violet-500/20">
          <Sparkles className="w-3.5 h-3.5" />AI生成
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />新建课程
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Course grid */}
      {loading ? (
        <div className="text-center py-12 text-xs text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin mx-auto mb-1" />加载中...</div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <GraduationCap className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">{search ? '没有匹配的课程' : '暂无课程'}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {filtered.map((course) => (
            <div
              key={course.id}
              className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
              onClick={() => fetchCourseDetail(course.id)}
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <GraduationCap className="w-4 h-4 text-primary shrink-0" />
                    <h3 className="text-sm font-medium truncate">{course.title}</h3>
                  </div>
                  {course.description && <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{course.description}</p>}
                  <div className="flex items-center gap-2 mt-2">
                    <Badge variant="bare" className={`${getStatusColor(course.status)}`}>
                      {getStatusLabel(course.status)}
                    </Badge>
                    {course.generated_by === 'ai' && (
                      <Badge variant="accent">AI</Badge>
                    )}
                    {course.domain && <span className="text-[10px] text-muted-foreground">{course.domain}</span>}
                  </div>
                </div>
                <Button
                  onClick={(e) => { e.stopPropagation(); handleDeleteCourse(course.id); }}
                  variant="icon-sm" className="hover:bg-red-500/10"
                  title="删除"
                >
                  <Trash2 className="w-3.5 h-3.5 text-red-500" />
                </Button>
              </div>
              {/* Progress */}
              <div className="mt-3 pt-3 border-t border-border">
                <div className="flex items-center justify-between text-[10px] mb-1">
                  <span className="text-muted-foreground">📚 {course.totalChapters} 章节 · ✅ {course.completedChapters} 完成</span>
                  <span className="font-medium">{getProgressPercent(course)}%</span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div className="h-1.5 rounded-full bg-primary transition-all" style={{ width: `${getProgressPercent(course)}%` }} />
                </div>
              </div>
              {course.tags.length > 0 && (
                <div className="flex items-center gap-1 mt-2">
                  {course.tags.slice(0, 3).map(t => <Badge key={t} variant="muted">{t}</Badge>)}
                  {course.tags.length > 3 && <span className="text-[10px] text-muted-foreground">+{course.tags.length - 3}</span>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">新建课程</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">课程标题 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.title} onChange={(e) => setCreateForm(f => ({ ...f, title: e.target.value }))} placeholder="Python 基础入门" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">描述</label>
                <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md h-20 resize-none" value={createForm.description} onChange={(e) => setCreateForm(f => ({ ...f, description: e.target.value }))} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">标签（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.tags} onChange={(e) => setCreateForm(f => ({ ...f, tags: e.target.value }))} placeholder="编程, Python" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">主题（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.topics} onChange={(e) => setCreateForm(f => ({ ...f, topics: e.target.value }))} />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">领域</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.domain} onChange={(e) => setCreateForm(f => ({ ...f, domain: e.target.value }))} placeholder="计算机科学" />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreate} disabled={!createForm.title.trim() || creating} variant="primary">
                  {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}创建
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* AI Generate Dialog */}
      {showGenerate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowGenerate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-purple-500" />
                <h2 className="text-sm font-medium">AI 生成课程</h2>
              </div>
              <Button onClick={() => setShowGenerate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">主题 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={generateForm.topic} onChange={(e) => setGenerateForm(f => ({ ...f, topic: e.target.value }))} placeholder="机器学习基础" onKeyDown={(e) => e.key === 'Enter' && handleGenerate()} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">章节数量</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" type="number" min="3" max="20" value={generateForm.num_chapters} onChange={(e) => setGenerateForm(f => ({ ...f, num_chapters: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">难度</label>
                  <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={generateForm.difficulty} onChange={(e) => setGenerateForm(f => ({ ...f, difficulty: e.target.value }))}>
                    <option value="beginner">入门</option>
                    <option value="intermediate">中级</option>
                    <option value="advanced">高级</option>
                  </select>
                </div>
              </div>
              <p className="text-[10px] text-muted-foreground">AI 将自动生成课程大纲、章节内容和测验题目。</p>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowGenerate(false)} variant="secondary">取消</Button>
                <Button onClick={handleGenerate} disabled={!generateForm.topic.trim() || generating} variant="chip-sm" className="flex items-center gap-1 bg-purple-500 text-white hover:opacity-90 disabled:opacity-50">
                  {generating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}{generating ? '生成中...' : '开始生成'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
