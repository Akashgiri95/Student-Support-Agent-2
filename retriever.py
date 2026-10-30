"""Policy retrieval (RAG): split the policy source into sections and return relevant ones."""
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import config
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

# Common document words carry no meaning for ranking
STOP_WORDS = list(ENGLISH_STOP_WORDS | {"student", "students", "term", "policy"})


class PolicyRetriever:
    def __init__(self, path=config.HANDBOOK_PATH):
        text = open(path, encoding="utf-8").read()
        # One chunk = one "## Heading" section of the policy source
        self.chunks = []
        for i, part in enumerate(re.split(r"\n## ", text)[1:], start=1):
            title, _, body = part.partition("\n")
            self.chunks.append({"chunk_id": f"C{i:02d}", "title": title.strip(), "text": body.strip()})
        docs = [c["title"] + " " + c["text"] for c in self.chunks]
        self.vectorizer = TfidfVectorizer(stop_words=STOP_WORDS, ngram_range=(1, 2))
        self.matrix = self.vectorizer.fit_transform(docs)

    def search(self, query, k=config.TOP_K_CHUNKS):
        scores = cosine_similarity(self.vectorizer.transform([query]), self.matrix)[0]
        ranked = scores.argsort()[::-1][:k]
        return [dict(self.chunks[i], score=round(float(scores[i]), 3))
                for i in ranked if scores[i] >= config.MIN_RETRIEVAL_SCORE]
