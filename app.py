"""Gradio two-panel chat interface for the legibility-demo workflow agent."""
import gradio as gr
from agent import make_agent


def respond(user_message: str, history: list, session: dict):
    if not user_message.strip():
        return "", history, "\n".join(session["event_log"]), session

    if session.get("agent") is None:
        session["agent"] = make_agent(session)

    result = session["agent"](user_message)
    response = str(result)

    history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": response},
    ]
    return "", history, "\n".join(session["event_log"]), session


def clear_session(session: dict):
    session["runner"] = None
    session["event_log"].clear()
    session["agent"] = None  # recreated fresh on next message
    return [], "", session


with gr.Blocks(title="Legibility Demo", fill_height=True) as demo:
    session = gr.State({"runner": None, "event_log": []})

    gr.Markdown("## Legibility Demo")

    with gr.Row():
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                label="Chat",
                height=560,
                autoscroll=True,
                show_label=False,
                placeholder="Ask me to run a workflow — e.g. *'What can I do?'* or *'Weather briefing for Keswick on 21 June'*",
            )
            msg = gr.Textbox(
                placeholder="Type a message and press Enter…",
                show_label=False,
                submit_btn=True,
                autoscroll=False,
            )
            clear_btn = gr.Button("Clear session", size="sm")

        with gr.Column(scale=2):
            event_log_box = gr.Textbox(
                label="Show Your Working",
                lines=32,
                max_lines=32,
                interactive=False,
                autoscroll=True,
            )

    msg.submit(
        respond,
        inputs=[msg, chatbot, session],
        outputs=[msg, chatbot, event_log_box, session],
    )
    clear_btn.click(
        clear_session,
        inputs=[session],
        outputs=[chatbot, event_log_box, session],
    )


if __name__ == "__main__":
    demo.launch()
