import { ConfirmHost } from "./components/ConfirmHost";
import { Toaster } from "./components/ui/sonner";
import { useEffect } from "react";
import { createBrowserRouter, createRoutesFromElements, Navigate, Route, RouterProvider, useRouteError } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AdminPage } from "./pages/AdminPage";
import { CourseDetailPage } from "./pages/CourseDetailPage";
import { CoursesPage } from "./pages/CoursesPage";
import { RegisterPage } from "./pages/RegisterPage";
import { LoginPage } from "./pages/LoginPage";
import { LtiLaunchPage } from "./pages/LtiLaunchPage";
import { LearnerChatPage } from "./pages/LearnerChatPage";
import { LearnFlashcardsPage } from "./pages/LearnFlashcardsPage";
import { LearnVideoPage } from "./pages/LearnVideoPage";
import { PageChange } from "./lib/pageChange";

const loadStudies = () => import("./pages/StudiesPage");
const loadModule = () => import("./pages/ModulePreviewPage");
const loadFlashcards = () => import("./pages/FlashcardEditorPage");
const loadVideo = () => import("./pages/VideoEditorPage");

// Route-level lazy: the router fetches a page's code before it leaves the current page, so the
// old page stays on screen meanwhile. A <Suspense> per route blanked the window instead.
const studies = async () => ({ Component: (await loadStudies()).StudiesPage });
const rating = async () => ({ Component: (await loadStudies()).BlindRatingPage });
// The module page's Markdown renderer is about a fifth of the bundle; no other page needs it first.
const modulePage = async () => ({ Component: (await loadModule()).ModulePreviewPage });
const flashcardEditor = async () => ({ Component: (await loadFlashcards()).FlashcardEditorPage });
const videoEditor = async () => ({ Component: (await loadVideo()).VideoEditorPage });

// The data router catches render errors itself; hand them on to ErrorBoundary, which knows the reload wording.
function RethrowRouteError(): never {
  throw useRouteError();
}

// A data router, so that an editor can ask before its unsaved edits are left (useBlocker).
const router = createBrowserRouter(createRoutesFromElements(
  <Route element={<PageChange />} errorElement={<RethrowRouteError />}>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/register" element={<RegisterPage />} />
    <Route path="/lti/launch" element={<LtiLaunchPage />} />
    <Route element={<ProtectedRoute adminOnly />}>
      <Route path="/admin" element={<AdminPage />} />
    </Route>
    <Route element={<ProtectedRoute />}>
      <Route path="/learn" element={<LearnerChatPage />} />
      <Route path="/learn/:courseId" element={<LearnerChatPage />} />
      <Route path="/learn/:courseId/flashcards/:setId" element={<LearnFlashcardsPage />} />
      <Route path="/learn/:courseId/videos/:videoId" element={<LearnVideoPage />} />
    </Route>
    <Route element={<ProtectedRoute authorOnly />}>
      <Route path="/studies" lazy={studies} />
      <Route path="/studies/:studyId" lazy={rating} />
      <Route path="/studies/:studyId/items/:itemId" lazy={rating} />
      <Route path="/courses" element={<CoursesPage />} />
      <Route path="/courses/:courseId" element={<CourseDetailPage />} />
      <Route path="/modules/:moduleId" lazy={modulePage} />
      <Route path="/flashcard-sets/:id" lazy={flashcardEditor} />
      <Route path="/videos/:id" lazy={videoEditor} />
    </Route>
    <Route path="*" element={<Navigate to="/courses" replace />} />
  </Route>,
));

// AuthProvider wraps the router: the routes need the session, and nothing in it needs the router.
function App() {
  // The split pages are fetched once the first screen is up, so the first visit to one after a
  // reload does not blank the whole window, sidebar included, while its code downloads.
  useEffect(() => {
    const prefetch = () => { for (const load of [loadStudies, loadModule, loadFlashcards, loadVideo]) void load().catch(() => {}); };
    const timer = window.setTimeout(prefetch, 1500);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <AuthProvider>
      <ErrorBoundary>
        <RouterProvider router={router} />
        <ConfirmHost />
        <Toaster position="bottom-right" closeButton />
      </ErrorBoundary>
    </AuthProvider>
  );
}

export default App;
