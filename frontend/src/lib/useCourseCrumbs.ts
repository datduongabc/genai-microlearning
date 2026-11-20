import { useEffect, useState } from "react";
import { listCourses } from "../api/courses";

export interface Crumb { label: string; to: string }

// The way back from a page inside a course: "Môn học" and the course by its name. Until the name
// arrives (or when it cannot be loaded) the course is still a link, by a generic label.
export function useCourseCrumbs(courseId: string | undefined): Crumb[] {
  const [name, setName] = useState<{ id: string; name: string } | null>(null);
  useEffect(() => {
    if (!courseId) return;
    let active = true;
    listCourses().then((courses) => {
      const course = Array.isArray(courses) ? courses.find((c) => c.id === courseId) : undefined;
      if (active && course) setName({ id: courseId, name: course.name });
    }, () => {});
    return () => { active = false; };
  }, [courseId]);
  const crumbs: Crumb[] = [{ label: "Môn học", to: "/courses" }];
  if (courseId) crumbs.push({ label: name?.id === courseId ? name.name : "Môn học này", to: `/courses/${courseId}` });
  return crumbs;
}
