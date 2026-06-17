export interface Project {
  id: string;
  title: string;
  category: string;
  description: string;
  challenge: string;
  solution: string;
  materials: string[];
  timeline: string;
  year: string;
  client: string;
}

export interface BlogPost {
  id: string;
  title: string;
  excerpt: string;
  content: string;
  category: string;
  date: string;
  readTime: string;
}
