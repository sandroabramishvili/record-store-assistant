"""Staff: the knowledge base the assistant answers from (/knowledge/articles), plus a search tester
that shows exactly which chunks a question would retrieve, with their similarity scores."""

import streamlit as st

import api_client as api
from components import call

TOPICS = ['grading', 'pressings', 'care', 'equipment', 'genre']

st.title('📖 Knowledge base')
articles_tab, search_tab = st.tabs(['Articles', 'Test a question'])

with articles_tab:
    articles = call(api.list_articles) or []
    st.caption(f'{len(articles)} articles. Saving re-indexes the article for the assistant.')
    for article in [None, *articles]:                       # None = the "new article" form
        label = '➕ New article' if article is None else \
            f'{article["title"]} · {article["topic"]} · {article["chunk_count"]} chunks' + \
            ('' if article['is_published'] else ' · unpublished')
        with st.expander(label), st.form(f'article-{article["id"] if article else "new"}'):
            title = st.text_input('Title', value=article['title'] if article else '')
            topic = st.selectbox('Topic', TOPICS, index=TOPICS.index(article['topic']) if article else 0)
            content = st.text_area('Content (Markdown)', value=article['content'] if article else '', height=260)
            published = st.checkbox('Published (the assistant may use it)', value=article['is_published'] if article else True)
            save, delete = st.columns([1, 5])
            if save.form_submit_button('Save', type='primary'):
                body = {'title': title.strip(), 'topic': topic, 'content': content.strip(), 'is_published': published}
                if call(api.save_article, body, article['id'] if article else None, success='Saved and re-indexed'):
                    st.rerun()
            if article and delete.form_submit_button('Delete'):
                if call(api.delete_article, article['id'], success='Deleted'):
                    st.rerun()

with search_tab:
    st.caption('What the assistant would retrieve for a question. Chunks below 0.6 similarity are ignored in chat.')
    question = st.text_input('Question', placeholder='What does VG+ mean?')
    if question.strip():
        for hit in call(api.search_knowledge, question.strip(), 5) or []:
            with st.container(border=True):
                st.markdown(f'**{hit["score"]:.2f}** · {hit["title"]} (chunk {hit["chunk"]})')
                st.caption(hit['text'])
