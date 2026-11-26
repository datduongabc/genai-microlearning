import { ConfirmHost } from "./components/ConfirmHost";
import { Toaster } from "./components/ui/sonner";
import { useEffect } from "react";
import { createBrowserRouter, createRoutesFromElements, Navigate, Route, RouterProvider, useRouteError } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { RegisterPage } from "./pages/RegisterPage";
import { LoginPage } from "./pages/LoginPage";
import { LtiLaunchPage } from "./pages/LtiLaunchPage";
import { PageChange } from "./lib/pageChange";

const loadStudies = () => import("./pages/StudiesPage");
const loadModule = () => import("./pages/ModulePreviewPage");
const loadFlashcards = () => import("./pages/FlashcardEditorPage");
const loadVideo = () => import("./pages/VideoEditorPage");
const loadAdmin = () => import("./pages/AdminPage");
const loadCourses = () => import("./pages/CoursesPage");
const loadCourse = () => import("./pages/CourseDetailPage");
const loadLearnerChat = () => import("./pages/LearnerChatPage");
const loadLearnFlashcards = () => import("./pages/LearnFlashcardsPage");
const loadLearnVideo = () => import("./pages/LearnVideoPage");

// Route-level lazy: the router fetches a page's code before it leaves the current page, so the
// old page stays on screen meanwhile. A <Suspense> per route blanked the window instead.
const studies = async () => ({ Component: (await loadStudies()).StudiesPage });
const rating = async () => ({ Component: (await loadStudies()).BlindRatingPage });
// Every page behind a sign-in is split off, so /login and the LTI launch load only the router and auth.
// The Markdown renderer (with KaTeX) is about a third of the code and only the chat and module pages use it.
const admin = async () => ({ Component: (await loadAdmin()).AdminPage });
const courses = async () => ({ Component: (await loadCourses()).CoursesPage });
const course = async () => ({ Component: (await loadCourse()).CourseDetailPage });
const learnerChat = async () => ({ Component: (await loadLearnerChat()).LearnerChatPage });
const learnFlashcards = async () => ({ Component: (await loadLearnFlashcards()).LearnFlashcardsPage });
const learnVideo = async () => ({ Component: (await loadLearnVideo()).LearnVideoPage });
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
      <Route path="/admin" lazy={admin} />
    </Route>
    <Route element={<ProtectedRoute />}>
      <Route path="/learn" lazy={learnerChat} />
      <Route path="/learn/:courseId" lazy={learnerChat} />
      <Route path="/learn/:courseId/flashcards/:setId" lazy={learnFlashcards} />
      <Route path="/learn/:courseId/videos/:videoId" lazy={learnVideo} />
    </Route>
    <Route element={<ProtectedRoute authorOnly />}>
      <Route path="/studies" lazy={studies} />
      <Route path="/studies/:studyId" lazy={rating} />
      <Route path="/studies/:studyId/items/:itemId" lazy={rating} />
      <Route path="/courses" lazy={courses} />
      <Route path="/courses/:courseId" lazy={course} />
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
    const prefetch = () => { for (const load of [loadCourses, loadCourse, loadStudies, loadModule, loadFlashcards, loadVideo, loadAdmin, loadLearnerChat, loadLearnFlashcards, loadLearnVideo]) void load().catch(() => {}); };
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
